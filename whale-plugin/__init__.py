from __future__ import division

import json
import random
import uuid
import threading
from datetime import datetime, timedelta
import jwt

from flask import Blueprint, render_template, request
from flask_apscheduler import APScheduler

from CTFd.plugins import register_plugin_assets_directory, register_admin_plugin_menu_bar
from CTFd.plugins.challenges import CHALLENGE_CLASSES
from CTFd.utils import user as current_user
from CTFd.utils.decorators import admins_only, authed_only
from .control_utils import ControlUtil
from .db_utils import DBUtils
from .redis_utils import RedisUtils
from .models import DynamicDockerChallenge, DynamicValueDockerChallenge, WhaleChallengeProgress, WhaleFlag, SequentialStaticFlag


_lock = threading.Lock()
_scheduler_started = False


def load(app):
    app.db.create_all()
    CHALLENGE_CLASSES["dynamic_docker"] = DynamicValueDockerChallenge
    
    from CTFd.plugins.flags import FLAG_CLASSES
    FLAG_CLASSES["whale"] = WhaleFlag
    FLAG_CLASSES["static"] = SequentialStaticFlag

    # Auto-migration for flags_required column
    from sqlalchemy import text as _sa_text, inspect as _sa_inspect
    try:
        if app.db.engine.name == 'sqlite':
            cursor = app.db.session.execute(_sa_text("PRAGMA table_info(dynamic_docker_challenge)"))
            columns = [row[1] for row in cursor.fetchall()]
            if 'flags_required' not in columns:
                app.db.session.execute(_sa_text("ALTER TABLE dynamic_docker_challenge ADD COLUMN flags_required INTEGER DEFAULT 1"))
                app.db.session.commit()
                print("[CTFd Whale] Added flags_required column to dynamic_docker_challenge table (SQLite)")
        elif app.db.engine.name == 'mysql':
            result = app.db.session.execute(_sa_text("SHOW COLUMNS FROM dynamic_docker_challenge LIKE 'flags_required'"))
            if not result.fetchone():
                app.db.session.execute(_sa_text("ALTER TABLE dynamic_docker_challenge ADD COLUMN flags_required INTEGER DEFAULT 1"))
                app.db.session.commit()
                print("[CTFd Whale] Added flags_required column to dynamic_docker_challenge table (MySQL)")
    except Exception as e:
        print(f"[CTFd Whale] Migration error: {e}")
        app.db.session.rollback()


    # Auto-migration for WhaleChallengeProgress table
    try:
        app.db.create_all()
        # Explicit check for table existence in case create_all missed it due to plugin loading order
        # has_table API varies by SQLAlchemy version — try both
        try:
            inspector = _sa_inspect(app.db.engine)
            _table_exists = inspector.has_table('whale_challenge_progress')
        except AttributeError:
            try:
                with app.db.engine.connect() as _c:
                    _table_exists = app.db.engine.dialect.has_table(_c, 'whale_challenge_progress')
            except Exception:
                _table_exists = True  # assume exists; create_all above handles it
        if not _table_exists:
            with app.db.engine.begin() as _conn:
                WhaleChallengeProgress.__table__.create(_conn)
    except Exception as e:
        print(f"[CTFd Whale] Progress table migration error: {e}")

    register_plugin_assets_directory(
        app, base_path="/plugins/ctfd-whale/assets/"
    )
    register_admin_plugin_menu_bar(
        title="Whale",
        route="/plugins/ctfd-whale/admin/settings"
    )

    page_blueprint = Blueprint(
        "ctfd-whale",
        __name__,
        template_folder="templates",
        static_folder="assets",
        url_prefix="/plugins/ctfd-whale"
    )
    import os
    import jinja2
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_template_dir = os.path.join(plugin_dir, 'templates')

    def render_plugin_template(template_name, **kwargs):
        """Render a template from the plugin's own template directory,
        falling back to CTFd's admin theme for base templates."""
        # Create a loader that checks plugin templates first, then CTFd themes
        loader = jinja2.ChoiceLoader([
            jinja2.FileSystemLoader(plugin_template_dir),
            app.jinja_loader,
        ])
        env = jinja2.Environment(loader=loader)
        # Copy Flask/CTFd template globals (url_for, etc.)
        env.globals.update(app.jinja_env.globals)
        template = env.get_template(template_name)
        return template.render(**kwargs)

    @page_blueprint.route('/admin/settings', methods=['GET'])
    @admins_only
    def admin_list_configs():
        configs = DBUtils.get_all_configs()
        return render_plugin_template('whale_config.html', configs=configs)

    @page_blueprint.route('/admin/settings', methods=['PATCH'])
    @admins_only
    def admin_save_configs():
        req = request.get_json()
        DBUtils.save_all_configs(req.items())
        redis_util = RedisUtils(app=app)
        redis_util.init_redis_port_sets()
        return json.dumps({'success': True})
    
    @page_blueprint.route('/flags/status', methods=['GET'])
    @authed_only
    def get_flag_status():
        challenge_id = request.args.get('challenge_id')
        user_id = current_user.get_current_user().id
        
        progress = WhaleChallengeProgress.query.filter_by(
            user_id=user_id,
            challenge_id=challenge_id
        ).all()
        
        solved_flag_ids = [p.flag_id for p in progress]
        return json.dumps({'success': True, 'solved_ids': solved_flag_ids})

    @page_blueprint.route('/flags/submit', methods=['POST'])
    @authed_only
    def submit_flag():
        data = request.get_json()
        challenge_id = data.get('challenge_id')
        submission = data.get('submission', '').strip()
        user_id = current_user.get_current_user().id

        # Get all flags, ordered
        from CTFd.models import Flags
        from CTFd.plugins.flags import get_flag_class, FLAG_CLASSES
        
        flags = Flags.query.filter_by(challenge_id=challenge_id).all()
        
        # Sort flags by explicit order
        def get_flag_order(f):
            data = f.data or ''
            # Try parsing as integer directly (WhaleFlag stores order as plain int)
            if data.strip().isdigit():
                return int(data.strip())
            # Try parsing as JSON (SequentialStaticFlag stores {"case":"...","order":N})
            try:
                meta = json.loads(data)
                if isinstance(meta, dict) and 'order' in meta:
                    return int(meta['order'])
            except (ValueError, TypeError):
                pass
            # Fallback: use database ID (large offset so unordered flags sort last)
            return f.id + 100000

        flags.sort(key=get_flag_order)
        
        
        # Check if flag_idx is provided (strict mode)
        flag_idx = data.get('flag_idx')
        
        matched_flag = None
        matched_index = -1
        
        if flag_idx is not None and str(flag_idx).isdigit():
            idx = int(flag_idx)
            if 0 <= idx < len(flags):
                target_flag = flags[idx]
                if get_flag_class(target_flag.type).compare(target_flag, submission):
                    matched_flag = target_flag
                    matched_index = idx
        else:
            # Fallback for old behavior (search all) - deprecated for sequential flow
            for i, flag in enumerate(flags):
                if get_flag_class(flag.type).compare(flag, submission):
                    matched_flag = flag
                    matched_index = i
                    break
        
        if matched_flag:
            # Check sequential order
            # Previous flags (0 to matched_index-1) must be in progress table
            for i in range(matched_index):
                prev_flag = flags[i]
                has_solved = WhaleChallengeProgress.query.filter_by(
                    user_id=user_id,
                    challenge_id=challenge_id,
                    flag_id=prev_flag.id
                ).first()
                if not has_solved:
                    return json.dumps({'success': False, 'msg': f'You must solve Flag {i+1} first!'})
            
            # Save progress if not already saved
            existing = WhaleChallengeProgress.query.filter_by(
                user_id=user_id,
                challenge_id=challenge_id,
                flag_id=matched_flag.id
            ).first()
            
            if not existing:
                prog = WhaleChallengeProgress(user_id=user_id, challenge_id=challenge_id, flag_id=matched_flag.id)
                app.db.session.add(prog)
                app.db.session.commit()
            
            return json.dumps({'success': True, 'msg': 'Correct!', 'flag_id': matched_flag.id})
            
        return json.dumps({'success': False, 'msg': 'Incorrect Flag'})


    @page_blueprint.route('/admin/images', methods=['GET'])
    @admins_only
    def admin_list_images():
        """List Docker images available on the host for challenge creation."""
        try:
            import docker as docker_lib
            configs = DBUtils.get_all_configs()
            client = docker_lib.DockerClient(
                base_url=configs.get("docker_api_url", "unix:///var/run/docker.sock")
            )
            images = client.images.list()
            result = []
            for img in images:
                for tag in img.tags:
                    result.append({
                        'id': img.short_id,
                        'tag': tag,
                        'size': round(img.attrs.get('Size', 0) / (1024 * 1024), 1),
                    })
            result.sort(key=lambda x: x['tag'])
            return json.dumps({'success': True, 'data': result})
        except Exception as e:
            return json.dumps({'success': False, 'msg': str(e)})


    @page_blueprint.route("/admin/containers", methods=['GET'])
    @admins_only
    def admin_list_containers():
        configs = DBUtils.get_all_configs()
        page = abs(request.args.get("page", 1, type=int))
        results_per_page = 50
        page_start = results_per_page * (page - 1)
        page_end = results_per_page * (page - 1) + results_per_page

        count = DBUtils.get_all_alive_container_count()
        containers = DBUtils.get_all_alive_container_page(page_start, page_end)

        pages = int(count / results_per_page) + (count % results_per_page > 0)
        return render_plugin_template("whale_containers.html", containers=containers, pages=pages, curr_page=page,
                               curr_page_start=page_start, configs=configs)

    @page_blueprint.route("/admin/containers", methods=['DELETE'])
    @admins_only
    def admin_delete_container():
        user_id = request.args.get('user_id')
        ControlUtil.remove_container(app, user_id)
        return json.dumps({'success': True})

    @page_blueprint.route("/admin/containers", methods=['PATCH'])
    @admins_only
    def admin_renew_container():
        user_id = request.args.get('user_id')
        challenge_id = request.args.get('challenge_id')
        DBUtils.renew_current_container(user_id=user_id, challenge_id=challenge_id)
        return json.dumps({'success': True})

    @page_blueprint.route('/container', methods=['POST'])
    @authed_only
    def add_container():
        user_id = current_user.get_current_user().id
        redis_util = RedisUtils(app=app, user_id=user_id)

        if not redis_util.acquire_lock():
            return json.dumps({'success': False, 'msg': 'Request Too Fast!'})

        if ControlUtil.frequency_limit():
            redis_util.release_lock()
            return json.dumps({'success': False, 'msg': 'Frequency limit, You should wait at least 1 min.'})

        try:
            ControlUtil.remove_container(app, user_id)
            challenge_id = request.args.get('challenge_id')
            ControlUtil.check_challenge(challenge_id, user_id)

            configs = DBUtils.get_all_configs()
            current_count = DBUtils.get_all_alive_container_count()
            if int(configs.get("docker_max_container_count", "100")) <= int(current_count):
                redis_util.release_lock()
                return json.dumps({'success': False, 'msg': 'Max container count exceed.'})

            dynamic_docker_challenge = DynamicDockerChallenge.query \
                .filter(DynamicDockerChallenge.id == challenge_id) \
                .first_or_404()
            flag = "flag{" + str(uuid.uuid4()) + "}"

            # Always use direct port mapping (no frp needed)
            port = redis_util.get_available_port()
            ControlUtil.add_container(app=app, user_id=user_id, challenge_id=challenge_id, flag=flag, port=port)

            redis_util.release_lock()
            return json.dumps({'success': True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CTFd Whale] Error in add_container: {e}")
            try:
                redis_util.release_lock()
            except Exception:
                pass
            return json.dumps({'success': False, 'msg': f'Error creating instance: {str(e)}'})

    @page_blueprint.route('/container', methods=['GET'])
    @authed_only
    def list_container():
        try:
            user_id = current_user.get_current_user().id
            challenge_id = request.args.get('challenge_id')
            ControlUtil.check_challenge(challenge_id, user_id)
            data = ControlUtil.get_container(user_id=user_id)
            configs = DBUtils.get_all_configs()
            timeout = int(configs.get("docker_timeout", "3600") or "3600")
            if data is not None:
                if int(data.challenge_id) != int(challenge_id):
                    return json.dumps({})
                dynamic_docker_challenge = DynamicDockerChallenge.query \
                    .filter(DynamicDockerChallenge.id == data.challenge_id) \
                    .first_or_404()

                host_ip = configs.get('frp_direct_ip_address', 'localhost')
                remaining = timeout - (datetime.now() - data.start_time).seconds

                return json.dumps({
                    'success': True,
                    'type': 'redirect',
                    'ip': host_ip,
                    'port': data.port,
                    'remaining_time': remaining,
                    'lan_domain': str(user_id) + "-" + data.uuid
                })
            else:
                return json.dumps({'success': True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CTFd Whale] Error in list_container: {e}")
            return json.dumps({'success': True})

    @page_blueprint.route('/container', methods=['DELETE'])
    @authed_only
    def remove_container():
        user_id = current_user.get_current_user().id
        redis_util = RedisUtils(app=app, user_id=user_id)
        if not redis_util.acquire_lock():
            return json.dumps({'success': False, 'msg': 'Request Too Fast!'})

        if ControlUtil.frequency_limit():
            return json.dumps({'success': False, 'msg': 'Frequency limit, You should wait at least 1 min.'})

        if ControlUtil.remove_container(app, user_id):
            redis_util.release_lock()
            return json.dumps({'success': True})
        else:
            return json.dumps({'success': False, 'msg': 'Failed when destroy instance, please contact admin!'})

    @page_blueprint.route('/container', methods=['PATCH'])
    @authed_only
    def renew_container():
        user_id = current_user.get_current_user().id
        redis_util = RedisUtils(app=app, user_id=user_id)
        if not redis_util.acquire_lock():
            return json.dumps({'success': False, 'msg': 'Request Too Fast!'})

        if ControlUtil.frequency_limit():
            return json.dumps({'success': False, 'msg': 'Frequency limit, You should wait at least 1 min.'})

        configs = DBUtils.get_all_configs()
        challenge_id = request.args.get('challenge_id')
        ControlUtil.check_challenge(challenge_id, user_id)
        docker_max_renew_count = int(configs.get("docker_max_renew_count", "5"))
        container = ControlUtil.get_container(user_id)
        if container is None:
            return json.dumps({'success': False, 'msg': 'Instance not found.'})
        if container.renew_count >= docker_max_renew_count:
            return json.dumps({'success': False, 'msg': 'Max renewal times exceed.'})
        ControlUtil.renew_container(user_id=user_id, challenge_id=challenge_id)
        redis_util.release_lock()
        return json.dumps({'success': True})

    # ============================================================
    # Kali Linux Machine Management
    # ============================================================
    KALI_IMAGE = 'ctfd-kali:latest'
    KALI_CONTAINER_PREFIX = 'ctfd-kali-'
    KALI_NOVNC_PORT_START = 7000
    KALI_VNC_PASS = 'kalipass'  # Default VNC password, same as in start.sh
    KALI_NOVNC_PORT_END = 7999
    KALI_SSO_SECRET = 'ctfd-kali-sso-secret-key-change-me'  # Change in production!

    def _get_docker_client():
        import docker as docker_lib
        configs = DBUtils.get_all_configs()
        return docker_lib.DockerClient(
            base_url=configs.get("docker_api_url", "unix:///var/run/docker.sock")
        )

    def _kali_container_name(username):
        safe = ''.join(c if c.isalnum() else '-' for c in username.lower())
        return KALI_CONTAINER_PREFIX + safe

    def _generate_sso_token(user_id, username, email=''):
        """Generate JWT SSO token for Kali Linux authentication"""
        try:
            payload = {
                'iss': 'ctfd',
                'sub': str(user_id),
                'user_id': user_id,
                'username': username,
                'email': email,
                'iat': datetime.utcnow(),
                'exp': datetime.utcnow() + timedelta(hours=8)  # 8 hour expiry
            }
            token = jwt.encode(payload, KALI_SSO_SECRET, algorithm='HS256')
            return token
        except Exception as e:
            print(f"[Kali SSO] Token generation error: {e}")
            return None

    def _find_free_kali_port(client):
        """Find a free port in the Kali range."""
        used_ports = set()
        try:
            for c in client.containers.list(all=True, filters={'name': KALI_CONTAINER_PREFIX}):
                ports = c.attrs.get('NetworkSettings', {}).get('Ports', {})
                for bindings in ports.values():
                    if bindings:
                        for b in bindings:
                            used_ports.add(int(b['HostPort']))
        except Exception:
            pass
        for port in range(KALI_NOVNC_PORT_START, KALI_NOVNC_PORT_END + 1):
            if port not in used_ports:
                return port
        return None

    def _make_novnc_url(host_ip, host_port, sso_token=None):
        """Generate noVNC URL with auto-login support"""
        # Always include autoconnect and VNC password for seamless login
        url = f"http://{host_ip}:{host_port}/vnc.html?autoconnect=true&resize=scale&password={KALI_VNC_PASS}"
        
        # Add SSO token as indicator that user is CTFd authenticated
        if sso_token:
            url += f"&sso_token={sso_token}"
        
        return url

    @page_blueprint.route('/kali/start', methods=['POST'])
    @authed_only
    def start_kali():
        """Create or start a per-user Kali Linux container."""
        try:
            user = current_user.get_current_user()
            username = user.name
            user_id = user.id
            container_name = _kali_container_name(username)
            client = _get_docker_client()

            # Check if container already exists
            try:
                existing = client.containers.get(container_name)
                if existing.status == 'running':
                    # Already running — return noVNC URL with SSO token
                    ports = existing.attrs['NetworkSettings']['Ports']
                    host_port = None
                    for key, bindings in ports.items():
                        if bindings:
                            host_port = int(bindings[0]['HostPort'])
                            break
                    configs = DBUtils.get_all_configs()
                    host_ip = configs.get('frp_direct_ip_address', 'localhost')
                    
                    # Generate fresh SSO token
                    sso_token = _generate_sso_token(user_id, username, user.email)
                    
                    return json.dumps({
                        'success': True, 'status': 'running',
                        'url': _make_novnc_url(host_ip, host_port, sso_token),
                        'msg': 'Kali machine is already running!'
                    })
                else:
                    # Exists but stopped — remove it and recreate
                    existing.remove(force=True)
            except Exception:
                pass  # Container doesn't exist, create new

            # Find a free port
            host_port = _find_free_kali_port(client)
            if not host_port:
                return json.dumps({'success': False, 'msg': 'No available ports for Kali machines.'})

            # Sanitize username for linux user (alphanumeric + hyphens, max 32 chars)
            safe_user = ''.join(c if c.isalnum() else '-' for c in username.lower())[:32]
            if not safe_user or not safe_user[0].isalpha():
                safe_user = 'kali-' + safe_user

            # Find a free port
            host_port = _find_free_kali_port(client)
            if not host_port:
                return json.dumps({'success': False, 'msg': 'No available ports for Kali machines.'})

            # Sanitize username for linux user (alphanumeric + hyphens, max 32 chars)
            safe_user = ''.join(c if c.isalnum() else '-' for c in username.lower())[:32]
            if not safe_user or not safe_user[0].isalpha():
                safe_user = 'kali-' + safe_user

            # Create new container with SSO support
            container = client.containers.run(
                KALI_IMAGE,
                name=container_name,
                hostname=safe_user,
                detach=True,
                environment={
                    'KALI_USER': safe_user,
                    'VNC_PASS': KALI_VNC_PASS,
                },
                ports={'6080/tcp': host_port},  # noVNC port
                mem_limit='2g',
                cpu_period=100000,
                cpu_quota=100000,  # 1 CPU core
                restart_policy={'Name': 'unless-stopped'},
                shm_size='512m',  # Needed for browser/desktop rendering
            )

            # Poll until the container is running (up to 15 s) rather than a blind sleep
            import time as _time
            _deadline = _time.time() + 15
            while _time.time() < _deadline:
                try:
                    container.reload()
                    if container.status == 'running':
                        break
                except Exception:
                    pass
                _time.sleep(1)

            configs = DBUtils.get_all_configs()
            host_ip = configs.get('frp_direct_ip_address', 'localhost')
            
            # Generate SSO token for auto-login
            sso_token = _generate_sso_token(user_id, username, user.email)

            return json.dumps({
                'success': True, 'status': 'created',
                'url': _make_novnc_url(host_ip, host_port, sso_token),
                'msg': f'Kali machine created for {username}!'
            })
        except Exception as e:
            import traceback; traceback.print_exc()
            return json.dumps({'success': False, 'msg': f'Error: {str(e)}'})


    @page_blueprint.route('/kali/status', methods=['GET'])
    @authed_only
    def kali_status():
        """Check the status of the user's Kali machine."""
        try:
            user = current_user.get_current_user()
            username = user.name
            user_id = user.id
            container_name = _kali_container_name(username)
            client = _get_docker_client()

            try:
                container = client.containers.get(container_name)
                status = container.status

                if status == 'running':
                    ports = container.attrs['NetworkSettings']['Ports']
                    host_port = None
                    for key, bindings in ports.items():
                        if bindings:
                            host_port = int(bindings[0]['HostPort'])
                            break
                    configs = DBUtils.get_all_configs()
                    host_ip = configs.get('frp_direct_ip_address', 'localhost')
                    
                    # Generate fresh SSO token
                    sso_token = _generate_sso_token(user_id, username, user.email)
                    
                    return json.dumps({
                        'success': True, 'status': 'running', 
                        'url': _make_novnc_url(host_ip, host_port, sso_token)
                    })
                else:
                    return json.dumps({'success': True, 'status': status, 'url': None})
            except Exception:
                return json.dumps({'success': True, 'status': 'not_found', 'url': None})
        except Exception as e:
            return json.dumps({'success': False, 'msg': str(e)})

    @page_blueprint.route('/kali/stop', methods=['DELETE'])
    @authed_only
    def stop_kali():
        """Stop and remove the user's Kali machine."""
        try:
            user = current_user.get_current_user()
            username = user.name
            container_name = _kali_container_name(username)
            client = _get_docker_client()

            try:
                container = client.containers.get(container_name)
                container.stop(timeout=5)
                container.remove(force=True)
                return json.dumps({'success': True, 'msg': 'Kali machine stopped and removed.'})
            except Exception:
                return json.dumps({'success': True, 'msg': 'No Kali machine found.'})
        except Exception as e:
            return json.dumps({'success': False, 'msg': str(e)})

    def auto_clean_container():
        with app.app_context():
            results = DBUtils.get_all_expired_container()
            for r in results:
                ControlUtil.remove_container(app, r.user_id)

    # The ctfd-target plugin owns the production Kali/target launcher UI.
    # Keep this legacy injector opt-in so it cannot add a second launcher on
    # /challenges and send users to the stale ctfd-whale endpoints.
    @app.after_request
    def inject_kali_script(response):
        if os.environ.get("CTFD_WHALE_INJECT_KALI", "0").lower() not in ("1", "true", "yes", "on"):
            return response
        if response.content_type and 'text/html' in response.content_type:
            try:
                data = response.get_data(as_text=True)
                inject_tag = '<script src="/plugins/ctfd-whale/assets/kali-inject.js"></script>'
                if '</body>' in data and inject_tag not in data:
                    data = data.replace('</body>', inject_tag + '\n</body>')
                    response.set_data(data)
            except Exception:
                pass
        return response

    app.register_blueprint(page_blueprint)

    global _scheduler_started
    with _lock:
        if not _scheduler_started:
            try:
                scheduler = APScheduler()
                scheduler.init_app(app)
                scheduler.start()
                scheduler.add_job(id='whale-auto-clean', func=auto_clean_container, trigger="interval", seconds=10)
                _scheduler_started = True

                redis_util = RedisUtils(app=app)
                redis_util.init_redis_port_sets()

                print("[CTFd Whale] Started successfully")
            except Exception as e:
                print(f"[CTFd Whale] Scheduler error: {e}")

import json
import traceback
import docker
from .db_utils import DBUtils
from .models import DynamicDockerChallenge


class DockerUtils:

    @staticmethod
    def add_new_docker_container(app, user_id, challenge_id, flag, uuid_code, port=0):
        configs = DBUtils.get_all_configs()

        dynamic_docker_challenge = DynamicDockerChallenge.query \
            .filter(DynamicDockerChallenge.id == challenge_id) \
            .first_or_404()

        try:
            client = docker.DockerClient(base_url=configs.get("docker_api_url", "unix:///var/run/docker.sock"))

            image_name = dynamic_docker_challenge.docker_image.strip()
            redirect_port = dynamic_docker_challenge.redirect_port or 80
            memory_limit = DockerUtils.convert_readable_text(dynamic_docker_challenge.memory_limit or "128m")
            cpu_limit = dynamic_docker_challenge.cpu_limit or 0.5

            container_name = f"whale-{user_id}-{uuid_code}"

            # Use plain Docker containers with direct port mapping
            container = client.containers.run(
                image=image_name,
                name=container_name,
                detach=True,
                ports={f'{redirect_port}/tcp': port if port != 0 else None},
                environment={'FLAG': flag},
                mem_limit=memory_limit if memory_limit > 0 else None,
                nano_cpus=int(cpu_limit * 1e9),
                labels={
                    'ctfd-whale': 'true',
                    'whale-user-id': str(user_id),
                    'whale-challenge-id': str(challenge_id),
                    'whale-uuid': uuid_code,
                },
                auto_remove=False,
            )

            # If port was 0, Docker assigned a random port — retrieve it
            if port == 0:
                container.reload()
                port_bindings = container.attrs['NetworkSettings']['Ports']
                host_port_info = port_bindings.get(f'{redirect_port}/tcp')
                if host_port_info:
                    assigned_port = int(host_port_info[0]['HostPort'])
                    # Update the DB record with the real port
                    from .models import WhaleContainer
                    from CTFd.models import db
                    record = WhaleContainer.query.filter_by(uuid=uuid_code).first()
                    if record:
                        record.port = assigned_port
                        db.session.commit()

            print(f"[CTFd Whale] Container {container_name} started on port {port}")

        except Exception as e:
            print(f"[CTFd Whale] Error creating container: {e}")
            traceback.print_exc()
            raise

    @staticmethod
    def convert_readable_text(text):
        if not text:
            return 0
        lower_text = str(text).lower().strip()

        if lower_text.endswith("k"):
            return int(lower_text[:-1]) * 1024

        if lower_text.endswith("m"):
            return int(lower_text[:-1]) * 1024 * 1024

        if lower_text.endswith("g"):
            return int(lower_text[:-1]) * 1024 * 1024 * 1024

        try:
            return int(lower_text)
        except ValueError:
            return 0

    @staticmethod
    def remove_current_docker_container(app, user_id, is_retry=False):
        configs = DBUtils.get_all_configs()
        container_record = DBUtils.get_current_containers(user_id=user_id)

        if container_record is None:
            return False

        try:
            client = docker.DockerClient(base_url=configs.get("docker_api_url", "unix:///var/run/docker.sock"))
            container_name = f"whale-{user_id}-{container_record.uuid}"

            try:
                container = client.containers.get(container_name)
                container.stop(timeout=5)
                container.remove(force=True)
                print(f"[CTFd Whale] Container {container_name} removed")
            except docker.errors.NotFound:
                print(f"[CTFd Whale] Container {container_name} not found (already removed)")
            except Exception as e:
                print(f"[CTFd Whale] Error stopping container {container_name}: {e}")
                if not is_retry:
                    return DockerUtils.remove_current_docker_container(app, user_id, True)

        except Exception as e:
            traceback.print_exc()
            if not is_retry:
                return DockerUtils.remove_current_docker_container(app, user_id, True)

        return True

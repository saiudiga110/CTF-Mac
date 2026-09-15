"""
Kali Instance Manager - Handles per-user Kali desktop instances
Manages container lifecycle, port allocation, and cleanup
"""

import docker
import logging
import random
from datetime import datetime, timedelta
from CTFd.models import db

logger = logging.getLogger(__name__)

class KaliInstanceManager:
    """Manages per-user Kali instances"""
    
    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            self.docker_client = None
    
    @staticmethod
    def get_available_port(start=6902, end=7900):
        """Find an available port for VNC"""
        from enhanced_features_plugin.extended_features import UserKaliInstance
        
        used_ports = db.session.query(UserKaliInstance.vnc_port).filter(
            UserKaliInstance.status == 'running'
        ).all()
        used_ports = [p[0] for p in used_ports]
        
        for _ in range(100):  # Try 100 times
            port = random.randint(start, end)
            if port not in used_ports:
                return port
        
        raise Exception("No available ports for Kali instances")
    
    def create_instance(self, user_id, lifetime_hours=4):
        """
        Create a new Kali instance for a user
        
        Args:
            user_id: CTFd user ID
            lifetime_hours: How long before instance expires
            
        Returns:
            {'port': vnc_port, 'url': vnc_url, 'container_id': container_id}
        """
        from enhanced_features_plugin.extended_features import UserKaliInstance
        
        if not self.docker_client:
            raise Exception("Docker not available")
        
        try:
            # Check for existing instance
            existing = db.session.query(UserKaliInstance).filter_by(
                user_id=user_id, status='running'
            ).first()
            
            if existing:
                return {
                    'port': existing.vnc_port,
                    'url': f'http://localhost:{existing.vnc_port}',
                    'container_id': existing.container_id,
                    'message': 'Existing instance reused'
                }
            
            # Get available port
            vnc_port = self.get_available_port()
            
            # Create container with unique labels
            container = self.docker_client.containers.run(
                'kali-ctf:latest',
                detach=True,
                ports={'6901/tcp': vnc_port},
                environment={
                    'VNC_PW': f'kali_user_{user_id}',
                    'KASM_LOG_LEVEL': 'warn',
                    'DISPLAY': ':1'
                },
                labels={
                    'ctfd.user.id': str(user_id),
                    'ctfd.instance.type': 'kali',
                    'ctfd.created': datetime.utcnow().isoformat()
                },
                shm_size='1g',  # Shared memory for display
                volumes={},
                tmpfs={'/tmp': 'size=512m'},
                cpus='1.0',  # CPU limit
                mem_limit='2g',  # Memory limit
                restart_policy={'Name': 'no'},
                networks=['lloydsctf_default']
            )
            
            # Record in database
            kali_instance = UserKaliInstance(
                user_id=user_id,
                container_id=container.id[:12],  # Short ID
                vnc_port=vnc_port,
                status='running',
                expires_at=datetime.utcnow() + timedelta(hours=lifetime_hours)
            )
            db.session.add(kali_instance)
            db.session.commit()
            
            logger.info(f"Created Kali instance for user {user_id} on port {vnc_port}")
            
            return {
                'port': vnc_port,
                'url': f'http://localhost:{vnc_port}',
                'container_id': container.id[:12],
                'expires_at': kali_instance.expires_at.isoformat()
            }
        
        except Exception as e:
            logger.error(f"Failed to create Kali instance for user {user_id}: {e}")
            raise
    
    def stop_instance(self, user_id):
        """Stop a user's Kali instance"""
        from enhanced_features_plugin.extended_features import UserKaliInstance
        
        try:
            instance = db.session.query(UserKaliInstance).filter_by(
                user_id=user_id, status='running'
            ).first()
            
            if instance:
                try:
                    container = self.docker_client.containers.get(instance.container_id)
                    container.stop(timeout=5)
                    container.remove()
                except:
                    pass  # Container might already be gone
                
                instance.status = 'stopped'
                db.session.commit()
                logger.info(f"Stopped Kali instance for user {user_id}")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Failed to stop Kali instance for user {user_id}: {e}")
            return False
    
    def cleanup_expired(self):
        """Remove expired Kali instances (background task)"""
        from enhanced_features_plugin.extended_features import UserKaliInstance
        
        try:
            expired_instances = db.session.query(UserKaliInstance).filter(
                UserKaliInstance.expires_at < datetime.utcnow(),
                UserKaliInstance.status == 'running'
            ).all()
            
            count = 0
            for instance in expired_instances:
                try:
                    container = self.docker_client.containers.get(instance.container_id)
                    container.stop(timeout=5)
                    container.remove()
                except:
                    pass
                
                instance.status = 'expired'
                count += 1
            
            if count > 0:
                db.session.commit()
                logger.info(f"Cleaned up {count} expired Kali instances")
            
            return count
        
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            return 0
    
    def get_instance_info(self, user_id):
        """Get info about a user's Kali instance"""
        from enhanced_features_plugin.extended_features import UserKaliInstance
        
        instance = db.session.query(UserKaliInstance).filter_by(
            user_id=user_id
        ).order_by(UserKaliInstance.allocated_at.desc()).first()
        
        if not instance:
            return None
        
        return {
            'port': instance.vnc_port,
            'url': f'http://localhost:{instance.vnc_port}',
            'status': instance.status,
            'allocated_at': instance.allocated_at.isoformat(),
            'expires_at': instance.expires_at.isoformat() if instance.expires_at else None
        }

# Singleton instance
kali_manager = None

def init_kali_manager():
    """Initialize the Kali manager"""
    global kali_manager
    kali_manager = KaliInstanceManager()
    return kali_manager

def get_kali_manager():
    """Get or create the Kali manager"""
    global kali_manager
    if kali_manager is None:
        kali_manager = KaliInstanceManager()
    return kali_manager

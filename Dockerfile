FROM ctfd/ctfd:3.8.6

USER root

# Freeze all current package versions, then install only new packages
# This prevents any existing package from being upgraded/downgraded
RUN pip freeze > /tmp/constraints.txt && \
    pip install "docker>=5.0.0" "Flask-APScheduler>=1.12.0" "flask-redis>=0.4.0" \
    "flask-socketio>=5.1.0" "python-socketio>=5.3.0" "requests>=2.27.0" "PyJWT>=2.8.0" \
    -c /tmp/constraints.txt

# Copy all plugins
COPY whale-plugin/ /opt/CTFd/CTFd/plugins/ctfd-whale/
COPY target-plugin/ /opt/CTFd/CTFd/plugins/ctfd-target/
COPY enhanced-features-plugin/ /opt/CTFd/CTFd/plugins/enhanced-features/
COPY ctfd/plugins/vbank_flags/ /opt/CTFd/CTFd/plugins/vbank_flags/
COPY ctf-live-plugin/ /opt/CTFd/CTFd/plugins/ctf-live/
COPY challenge-catalog.json /opt/CTFd/challenge-catalog.json

USER 1001

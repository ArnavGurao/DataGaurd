#!/bin/bash
# Invoked by SSM after the immutable release archive has been downloaded.
set -euo pipefail
release_dir=$1
region=$2
prefix=$3
log_group=$4
public_ip=$5
topic_arn=$6
cd "$release_dir"

# User data installs these on first boot. SSM can register before cloud-init
# has finished, so explicitly wait for it rather than racing yum and docker.
if ! cloud-init status --wait >/dev/null; then
    echo 'Initial package setup failed; repairing the required host packages.'
    dnf install -y docker nginx
    systemctl enable --now docker amazon-ssm-agent
fi
systemctl is-active --quiet docker
docker build --pull -t "dataguard:$(basename "$release_dir")" -f deploy/aws/Dockerfile .
install -d -m 755 /opt/dataguard /etc/dataguard
printf 'APP_ENV=aws\nAWS_REGION=%s\nSSM_PARAMETER_PREFIX=%s\n' "$region" "$prefix" > /etc/dataguard/runtime.env
chmod 600 /etc/dataguard/runtime.env

# Bootstrap administrator credentials live in a short-lived, encrypted S3
# object readable only through this instance's bootstrap prefix. They are
# streamed into the container and never saved on the instance filesystem.
if [[ -n "${BOOTSTRAP_URI:-}" ]]; then
    aws s3 cp "$BOOTSTRAP_URI" - --region "$region" --only-show-errors | \
        docker run --rm -i --network host --env-file /etc/dataguard/runtime.env \
        "dataguard:$(basename "$release_dir")" python init_database.py
fi

previous=$(readlink -f /opt/dataguard/current || true)
# Build completes before the running application is interrupted.
systemctl stop dataguard-api dataguard-worker 2>/dev/null || true
docker run --rm --network host --env-file /etc/dataguard/runtime.env \
    "dataguard:$(basename "$release_dir")" python -m alembic upgrade head

for service in api worker; do
    if [[ "$service" == api ]]; then
        command='python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1'
    else
        command='python -m app.worker'
    fi
    cat > "/etc/systemd/system/dataguard-$service.service" <<EOF
[Unit]
Description=DataGuard $service
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
[Service]
Restart=always
RestartSec=10
TimeoutStopSec=180
ExecStartPre=-/usr/bin/docker rm dataguard-$service
ExecStart=/usr/bin/docker run --rm --name dataguard-$service --network host --read-only --tmpfs /tmp:size=128m --cap-drop ALL --security-opt no-new-privileges --env-file /etc/dataguard/runtime.env --log-driver awslogs --log-opt awslogs-region=$region --log-opt awslogs-group=$log_group --log-opt awslogs-stream=$service --log-opt mode=non-blocking dataguard:$(basename "$release_dir") $command
ExecStop=/usr/bin/docker stop --time 150 dataguard-$service
[Install]
WantedBy=multi-user.target
EOF
done

ln -sfn "$release_dir" /opt/dataguard/current
install -m 644 deploy/aws/proxy.conf /etc/nginx/dataguard-proxy.conf
# Amazon Linux ships a default server block in nginx.conf. Use one explicit
# include so the packaged sample virtual host cannot shadow this application.
cat > /etc/nginx/nginx.conf <<'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /run/nginx.pid;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    include /etc/nginx/conf.d/*.conf;
}
EOF

# Obtain a trusted IP-address certificate, supported by Certbot 5.4+.
# Serve only HTTP validation until issuance succeeds; never expose login on HTTP.
install -d -m 755 /var/lib/letsencrypt
if [[ ! -f /etc/letsencrypt/live/dataguard-ip/fullchain.pem ]]; then
    cat > /etc/nginx/conf.d/dataguard.conf <<EOF
server {
    listen 80 default_server;
    server_name $public_ip;
    location ^~ /.well-known/acme-challenge/ { root /var/lib/letsencrypt; }
    location / { return 503; }
}
EOF
    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx
    docker run --rm --name dataguard-certbot \
        -v /etc/letsencrypt:/etc/letsencrypt -v /var/lib/letsencrypt:/var/lib/letsencrypt \
        certbot/certbot:v5.8.0 certonly --non-interactive --agree-tos \
        --register-unsafely-without-email --webroot --webroot-path /var/lib/letsencrypt \
        --preferred-profile shortlived --ip-address "$public_ip" --cert-name dataguard-ip
fi
sed "s/__PUBLIC_IP__/$public_ip/g" deploy/aws/nginx.conf > /etc/nginx/conf.d/dataguard.conf

# IP certificates last about six days. Renew four times a day, reload Nginx,
# and publish an operational alert if renewal fails. Persist certificate data
# outside the containers so it survives releases, restarts, and image changes.
cat > /etc/systemd/system/dataguard-tls-renew.service <<EOF
[Unit]
Description=Renew DataGuard HTTPS certificate
Requires=docker.service
After=docker.service network-online.target
OnFailure=dataguard-tls-alert.service
[Service]
Type=oneshot
ExecStart=/usr/bin/docker run --rm --name dataguard-certbot-renew -v /etc/letsencrypt:/etc/letsencrypt -v /var/lib/letsencrypt:/var/lib/letsencrypt certbot/certbot:v5.8.0 renew --quiet
ExecStartPost=/usr/sbin/nginx -s reload
EOF
cat > /etc/systemd/system/dataguard-tls-renew.timer <<'EOF'
[Unit]
Description=Check DataGuard HTTPS renewal every six hours
[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
RandomizedDelaySec=600
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/dataguard-tls-alert.service <<EOF
[Unit]
Description=Alert on DataGuard HTTPS renewal failure
[Service]
Type=oneshot
ExecStart=/usr/bin/aws sns publish --region $region --topic-arn $topic_arn --subject "DataGuard HTTPS renewal failed" --message "Certificate renewal failed on $public_ip. Check dataguard-tls-renew.service using Systems Manager before the certificate expires."
EOF
nginx -t
systemctl daemon-reload
systemctl enable dataguard-api dataguard-worker nginx dataguard-tls-renew.timer
systemctl start dataguard-tls-renew.timer
systemctl restart dataguard-api dataguard-worker
ready=0
for attempt in $(seq 1 60); do
    if curl --fail --silent http://127.0.0.1:8000/api/ready >/dev/null; then ready=1; break; fi
    sleep 2
done
if [[ "$ready" != 1 ]]; then
    echo "Readiness failed. Previous release: $previous. Review CloudWatch logs before rollback."
    exit 1
fi
systemctl restart nginx
systemctl is-active dataguard-api dataguard-worker nginx
echo "Release installed and database readiness passed."

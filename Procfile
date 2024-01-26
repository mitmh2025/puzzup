release: python manage.py migrate
web: gunicorn --max-requests 300 --max-requests-jitter 50 puzzup.wsgi
worker: celery -A puzzup worker --concurrency 2

web: gunicorn core.wsgi:application
worker: celery -A core worker --loglevel=info
beat: celery -A core beat --loglevel=info

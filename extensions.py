from flask_sqlalchemy import SQLAlchemy
from redis import Redis, ConnectionError
from rq import Queue
import os

db = SQLAlchemy()

# 初始化為 None
redis_conn = None
compression_queue = None

def init_redis():
    global redis_conn, compression_queue
    try:
        if os.environ.get('FLASK_ENV') == 'production':
            redis_url = 'redis://redis:6379'
        else:
            redis_url = 'redis://localhost:6379'
        
        redis_conn = Redis.from_url(redis_url, socket_connect_timeout=2)
        redis_conn.ping()  # 測試連接
        compression_queue = Queue('compression', connection=redis_conn)
        print("成功連接到 Redis")
    except (ConnectionError, Exception) as e:
        print(f"無法連接到 Redis: {e}")
        print("將使用同步處理模式")
        redis_conn = None
        compression_queue = None 
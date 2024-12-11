from flask_sqlalchemy import SQLAlchemy
from redis import Redis, ConnectionError
from rq import Queue
import os
import time

db = SQLAlchemy()

# 初始化為 None
redis_conn = None
compression_queue = None

def init_redis(max_retries=5, retry_delay=5):
    global redis_conn, compression_queue
    
    redis_url = os.environ.get('REDIS_URL', 'redis://redis:6379')
    print(f"使用 Redis URL: {redis_url}")
    
    for attempt in range(max_retries):
        try:
            print(f"嘗試連接 Redis (第 {attempt + 1} 次)")
            
            # 簡化連接選項
            connection_kwargs = {
                'socket_timeout': 5,
                'socket_connect_timeout': 5,
                'retry_on_timeout': True
            }
            
            # 創建連接
            redis_conn = Redis.from_url(
                redis_url,
                **connection_kwargs
            )
            
            # 測試連接
            if redis_conn.ping():
                print("Redis ping 成功")
                
                # 創建隊列
                compression_queue = Queue(
                    'compression',
                    connection=redis_conn
                )
                
                print(f"隊列創建成功，當前任務數: {len(compression_queue)}")
                return True
                
        except Exception as e:
            print(f"Redis 初始化錯誤 (第 {attempt + 1} 次): {str(e)}")
            if attempt < max_retries - 1:
                print(f"等待 {retry_delay} 秒後重試...")
                time.sleep(retry_delay)
            else:
                print("已達到最大重試次數，初始化失敗")
                redis_conn = None
                compression_queue = None
    
    return False

def get_redis():
    return redis_conn

def get_queue():
    return compression_queue
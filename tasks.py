import psutil
import time
from config import VIDEO_STATUS, MAX_CONCURRENT_TASKS
from extensions import db, compression_queue
from app import app, compress_video
from models import Video

def can_start_compression():
    """檢查系統資源是否允許開始新的壓縮任務"""
    cpu_percent = psutil.cpu_percent(interval=1)
    
    # 如果沒有壓縮隊列，只檢查 CPU 使用率
    if compression_queue is None:
        return cpu_percent < 80
        
    running_tasks = len(compression_queue.started_job_registry)
    return cpu_percent < 80 and running_tasks < MAX_CONCURRENT_TASKS

def compress_video_task(filename):
    """壓縮任務處理函數"""
    print(f"開始處理壓縮任務: {filename}")
    with app.app_context():
        video = Video.query.filter_by(filename=filename).first()
        if not video:
            print(f"找不到影片記錄: {filename}")
            return
        
        try:
            video.status = VIDEO_STATUS['COMPRESSING']
            db.session.commit()
            print(f"更新狀態為壓縮中: {filename}")
            
            # 等待系統資源用
            while not can_start_compression():
                print(f"等待系統資源... CPU使用率: {psutil.cpu_percent()}%")
                time.sleep(5)
            
            # 進行壓縮
            success = compress_video(video.original_path, video.compressed_path)
            print(f"壓縮結果: {'成功' if success else '失敗'}")
            
            if success:
                video.status = VIDEO_STATUS['COMPRESSED']
            else:
                video.status = VIDEO_STATUS['UPLOADED']
                
            db.session.commit()
            print(f"更新最終狀態為: {video.status}")
            
        except Exception as e:
            print(f"壓縮過程出錯: {str(e)}")
            video.status = VIDEO_STATUS['UPLOADED']
            db.session.commit() 

def test_connection():
    """用於測試 Redis 連接的函數"""
    return "Connection test successful"
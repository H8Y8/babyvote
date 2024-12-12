from datetime import datetime
from extensions import db

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    title = db.Column(db.String(255))
    votes = db.Column(db.Integer, default=0)
    views = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='uploaded')
    in_use = db.Column(db.Boolean, default=False)
    original_path = db.Column(db.String(255))
    compressed_path = db.Column(db.String(255))
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('video.id'), nullable=False)
    ip_address = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 添加唯一約束，確保每個 IP 只能對同一個影片投一次票
    __table_args__ = (db.UniqueConstraint('video_id', 'ip_address', name='unique_vote'),)

class View(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('video.id'))
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow) 
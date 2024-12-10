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
    video_id = db.Column(db.Integer, db.ForeignKey('video.id'))
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow) 
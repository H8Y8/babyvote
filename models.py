from datetime import datetime
from extensions import db
import os
from flask import current_app
from config import VIDEO_STATUS

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    title = db.Column(db.String(255), nullable=True)
    votes = db.Column(db.Integer, default=0)
    views = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default=VIDEO_STATUS['UPLOADED'])
    in_use = db.Column(db.Boolean, default=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Video {self.filename}>'

    @property
    def original_path(self):
        return os.path.join(current_app.config['UPLOAD_FOLDER'], self.filename)

    @property
    def compressed_path(self):
        name, ext = os.path.splitext(self.filename)
        return os.path.join(current_app.config['UPLOAD_FOLDER'], f"{name}_compressed{ext}")

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('video.id'))
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow) 
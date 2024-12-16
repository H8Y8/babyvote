import cv2
import os

def generate_thumbnail(video_path, output_path, frame_time=0.1):
    """
    從影片生成縮圖
    :param video_path: 影片檔案路徑
    :param output_path: 輸出縮圖路徑
    :param frame_time: 要擷取的時間點（秒）
    :return: bool 是否成功生成縮圖
    """
    try:
        # 確保輸出目錄存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 開啟影片檔
        cap = cv2.VideoCapture(video_path)
        
        # 獲取影片的 FPS
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        # 計算要擷取的幀數
        frame_number = int(fps * frame_time)
        
        # 設定要讀取的幀
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        
        # 讀取該幀
        ret, frame = cap.read()
        
        if ret:
            # 調整圖片大小為 9:16 比例，寬度 270 像素
            target_width = 270
            target_height = int(target_width * 16 / 9)
            thumbnail = cv2.resize(frame, (target_width, target_height))
            
            # 儲存縮圖
            cv2.imwrite(output_path, thumbnail)
            
            # 釋放資源
            cap.release()
            return True
        else:
            cap.release()
            return False
            
    except Exception as e:
        print(f"生成縮圖時發生錯誤: {str(e)}")
        return False 
import cv2
import requests
import time
import numpy as np
from ultralytics import YOLO
from shapely.geometry import Polygon, Point

# --- 1. CONFIGURATION ---
API_URL = "https://smart-parking-backend-1cvq.onrender.com/api/cv/bulk_update"


VIDEO_SOURCE = 0


# Approximate coordinates for a 1920x1080 video
PARKING_ZONES = {
    # Top Row (Zone A)
    "A1": Polygon([(200, 150), (450, 150), (450, 450), (200, 450)]), 
    "A2": Polygon([(460, 150), (710, 150), (710, 450), (460, 450)]),
    "A3": Polygon([(720, 150), (970, 150), (970, 450), (720, 450)]),
    "A4": Polygon([(980, 150), (1230, 150), (1230, 450), (980, 450)]),
    "A5": Polygon([(1240, 150), (1490, 150), (1490, 450), (1240, 450)]),
    
    # Bottom Row (Zone B)
    "B1": Polygon([(200, 600), (450, 600), (450, 900), (200, 900)]), 
    "B2": Polygon([(460, 600), (710, 600), (710, 900), (460, 900)]),
    "B3": Polygon([(720, 600), (970, 600), (970, 900), (720, 900)]),
    "B4": Polygon([(980, 600), (1230, 600), (1230, 900), (980, 900)]),
    "B5": Polygon([(1240, 600), (1490, 600), (1490, 900), (1240, 900)])
}

current_spot_states = {spot_id: "free" for spot_id in PARKING_ZONES.keys()}

model = YOLO('yolov8n.pt') 

# --- 2. LIVE FEED PROCESSING ---
cap = cv2.VideoCapture(VIDEO_SOURCE)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        print("End of video stream or file not found.")
        break

    # Run YOLO detection for 'cars' (Class 2 in COCO dataset)
    results = model(frame, classes=[2], verbose=False)
    
    # Extract car bounding boxes
    car_boxes = []
    for r in results:
        boxes = r.boxes
        for box in boxes:
            # Safely convert PyTorch tensors to standard numbers for Shapely
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy() 
            car_boxes.append(Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)]))

    # --- 3. STATUS LOGIC ---
    state_changed = False
    updates_to_send = []

    for spot_id, spot_polygon in PARKING_ZONES.items():
        is_occupied = False
        
        # Extract coordinates for OpenCV drawing
        pts = spot_polygon.exterior.coords[:-1]
        
        for car in car_boxes:
            # If the car overlaps the spot by more than 30%, it's parked there
            intersection_area = spot_polygon.intersection(car).area
            if intersection_area / spot_polygon.area > 0.3:
                is_occupied = True
                break

        new_status = "occupied" if is_occupied else "free"
        color = (0, 0, 255) if is_occupied else (255, 0, 0)
        
        # Draw bounding boxes for visualization
        cv2.polylines(frame, [np.array(pts, np.int32)], True, color, 2)
        cv2.putText(frame, spot_id, (int(pts[0][0]), int(pts[0][1])-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # Check if the state changed from the last frame
        if current_spot_states[spot_id] != new_status:
            current_spot_states[spot_id] = new_status
            updates_to_send.append({"spot_id": spot_id, "status": new_status})
            state_changed = True

    # --- 4. SEND DATA TO BACKEND ---
    if state_changed and updates_to_send:
        try:
            # Send the bulk update to your FastAPI server
            response = requests.post(API_URL, json={"updates": updates_to_send})
            print(f"CV Update Sent: {updates_to_send} | Server: {response.status_code}")
        except Exception as e:
            print(f"Failed to reach backend: {e}")

    # --- 5. CLOUD / HEADLESS OUTPUT ---
    # Silently save the image to the server instead of opening a window
    cv2.imwrite("latest_detection_test.jpg", frame)
    print("Processed frame saved. Check your logs/dashboard.")

    # Prevent the loop from running too fast on a cloud server and rate-limiting the API
    time.sleep(0.5)

cap.release()
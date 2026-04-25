import cv2
import requests
import time
from ultralytics import YOLO
from shapely.geometry import Polygon, Point

# --- 1. CONFIGURATION ---
API_URL = "https://smart-parking-backend-1cvq.onrender.com/api/cv/bulk_update"
VIDEO_SOURCE = 0

PARKING_ZONES = {
    "A1": Polygon([(50, 100), (150, 100), (150, 250), (50, 250)]), 
    "A2": Polygon([(160, 100), (260, 100), (260, 250), (160, 250)])  
}

current_spot_states = {spot_id: "free" for spot_id in PARKING_ZONES.keys()}

model = YOLO('yolov8n.pt') 

# --- 2. LIVE FEED PROCESSING ---
cap = cv2.VideoCapture(VIDEO_SOURCE)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break # End of video or camera disconnected

    # Run YOLO detection for 'cars' (Class 2 in COCO dataset)
    results = model(frame, classes=[2], verbose=False)
    
    # Extract car bounding boxes
    car_boxes = []
    for r in results:
        boxes = r.boxes
        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0]
            car_boxes.append(Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)]))

    # --- 3. STATUS LOGIC ---
    state_changed = False
    updates_to_send = []

    for spot_id, spot_polygon in PARKING_ZONES.items():
        is_occupied = False
        
        # Draw the parking spot on the frame (Blue for free, Red for occupied)
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
        cv2.polylines(frame, [__import__('numpy').array(pts, __import__('numpy').int32)], True, color, 2)
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
            print(f"CV Update Sent: {updates_to_send} | Server response: {response.status_code}")
        except Exception as e:
            print(f"Failed to reach backend: {e}")

    # Show the live feed window
    cv2.imshow('Nexus Smart Parking - Live CV Feed', frame)

    # Press 'q' to quit the window
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
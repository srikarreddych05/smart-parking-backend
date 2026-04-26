from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import psycopg2
import os
from psycopg2.extras import RealDictCursor
from passlib.context import CryptContext
import smtplib
import random
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

# --- 1. SECURITY & DATABASE CONFIGURATION ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db_connection():
    # In production, this reads the live cloud database URL. 
    # Locally, it will read your .env file.
    db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:YOUR_ACTUAL_PASSWORD_HERE@127.0.0.1:5432/smart_parking")
    return psycopg2.connect(db_url)

# --- AUTO CLEANUP TASK ---
def check_expired_bookings():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Find active bookings that have passed their end_time
        cursor.execute("""
            UPDATE bookings 
            SET status = 'Completed' 
            WHERE status = 'Active' AND end_time <= NOW()
            RETURNING spot_id
        """)
        expired_spots = cursor.fetchall()
        
        # Free up the parking spots for those expired bookings
        for spot in expired_spots:
            cursor.execute("""
                UPDATE spots 
                SET status = 'free', plate = NULL, is_overstay = false 
                WHERE id = %s AND status = 'occupied'
            """, (spot[0],))
            
            # Note: The websocket broadcast will happen on the next frontend refresh, 
            # or we can add an async broadcast here later!
        
        conn.commit()
        if expired_spots:
            print(f"Cleaned up {len(expired_spots)} expired bookings.")
    except Exception as e:
        conn.rollback()
        print(f"Scheduler Error: {e}")
    finally:
        cursor.close()
        conn.close()

# --- STARTUP CHECKER & SCHEDULER ---
@app.on_event("startup")
def startup_db_check():
    try:
        conn = get_db_connection()
        conn.close()
        print("\n✅ SUCCESS: DATABASE CONNECTED PERFECTLY!\n")
        
        # Start the alarm clock
        scheduler = BackgroundScheduler()
        scheduler.add_job(check_expired_bookings, 'interval', minutes=1)
        scheduler.start()
        print("✅ SUCCESS: AUTO-EXPIRY SCHEDULER STARTED!\n")
        
    except Exception as e:
        print("\n❌ CRITICAL ERROR: DATABASE CONNECTION FAILED!")
        print(f"❌ Details: {e}")
        
# --- 2. CORS SETTINGS ---
origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "https://my-frontend-steel-two.vercel.app" 
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 3. WEBSOCKET CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)

manager = ConnectionManager()

# --- 4. DATA MODELS ---

class CVUpdate(BaseModel):
    spot_id: str
    status: str

class CVBulkUpdateRequest(BaseModel):
    updates: List[CVUpdate]
    
class LoginCredentials(BaseModel):
    email: str
    password: str

class OTPRequest(BaseModel):
    email: str

class RegisterData(BaseModel):
    name: str
    email: str
    password: str
    otp: str  
    carNumber: Optional[str] = ""
    employeeId: Optional[str] = ""

class BookingRequest(BaseModel):
    spot_id: str
    user_id: int
    start_time: str
    plate: Optional[str] = None

class EndBookingRequest(BaseModel):
    spot_id: str
    user_id: int

class SpotUpdateRequest(BaseModel):
    status: str

class UserUpdateRequest(BaseModel):
    name: str
    email: str
    plate: str

class EmergencyRequest(BaseModel):
    active: bool

# --- 5. AUTHENTICATION ENDPOINTS ---

@app.post("/api/send-otp")
def send_otp(req: OTPRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Check if email exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (req.email.lower(),))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # 2. Generate OTP
        otp_code = str(random.randint(100000, 999999))
        expiry = datetime.now() + timedelta(minutes=10)
        
        # 3. Save to database (Upsert)
        cursor.execute("""
            INSERT INTO otp_codes (email, otp, expires_at) 
            VALUES (%s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET otp = EXCLUDED.otp, expires_at = EXCLUDED.expires_at
        """, (req.email.lower(), otp_code, expiry))
        conn.commit()
        
        # 4. Send Email
        sender_email = "srikarreddy701@gmail.com" 
        sender_password = os.environ.get("EMAIL_APP_PASSWORD") 

        if not sender_password:
            raise HTTPException(status_code=500, detail="Server email configuration is missing")

        msg = MIMEText(f"Your Nexus Parking verification code is: {otp_code}. It expires in 10 minutes.")
        msg['Subject'] = 'Registration Verification Code'
        msg['From'] = sender_email
        msg['To'] = req.email.lower()

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            
        return {"message": "OTP sent successfully!"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/register")
def register(data: RegisterData):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Verify OTP
        cursor.execute("SELECT otp, expires_at FROM otp_codes WHERE email = %s", (data.email.lower(),))
        otp_record = cursor.fetchone()
        
        if not otp_record:
            raise HTTPException(status_code=400, detail="Please request an OTP first")
        if otp_record['otp'] != data.otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")
        if otp_record['expires_at'] < datetime.now():
            raise HTTPException(status_code=400, detail="OTP has expired")

        # 2. Hash Password
        safe_password = data.password[:72]
        hashed_password = pwd_context.hash(safe_password)
        
        # 3. FORCE ROLE TO DRIVER
        role = "driver"
        plate = data.carNumber.upper()
        
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, role, plate, employee_id, balance) 
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, name, email, role, plate, balance
            """,
            (data.name, data.email.lower(), hashed_password, role, plate, data.employeeId, 50.00)
        )
        new_user = cursor.fetchone()
        
        # 4. Delete used OTP
        cursor.execute("DELETE FROM otp_codes WHERE email = %s", (data.email.lower(),))
        conn.commit()
        
        return {"user": new_user, "message": "Registration successful"}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/login")
def login(credentials: LoginCredentials):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (credentials.email.lower(),))
        user = cursor.fetchone()
        
        # FIX: Truncate the password to 72 characters max before verifying
        safe_password = credentials.password[:72]
        
        if not user or not pwd_context.verify(safe_password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        del user["password_hash"]
        return {"user": user}
   
    finally:
        cursor.close()
        conn.close()

@app.put("/api/users/{user_id}")
def update_user(user_id: int, data: UserUpdateRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute(
            "UPDATE users SET name = %s, email = %s, plate = %s WHERE id = %s RETURNING id, name, email, role, plate, balance",
            (data.name, data.email, data.plate, user_id)
        )
        updated_user = cursor.fetchone()
        if not updated_user:
            raise HTTPException(status_code=404, detail="User not found")
            
        conn.commit()
        return {"user": updated_user, "message": "Profile updated successfully"}
    finally:
        cursor.close()
        conn.close()

# --- 6. PARKING & BOOKING ENDPOINTS ---


@app.post("/api/cv/bulk_update")
def cv_bulk_update(req: CVBulkUpdateRequest, bg_tasks: BackgroundTasks):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for update in req.updates:
            # We use 'SYS-CV' to show the AI updated this, not a manual admin
            plate = "SYS-CV" if update.status == "occupied" else None
            
            cursor.execute(
                "UPDATE spots SET status = %s, plate = %s WHERE id = %s AND status != 'maintenance'",
                (update.status, plate, update.spot_id)
            )
            
            # Broadcast each change to the React map
            bg_tasks.add_task(manager.broadcast, {
                "type": "SPOT_UPDATE",
                "spot_id": update.spot_id,
                "updates": {"status": update.status, "plate": plate, "isOverstay": False}
            })
            
        conn.commit()
        return {"message": f"Successfully updated {len(req.updates)} spots via CV"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
@app.get("/api/spots")
def get_spots():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("SELECT id, zone, status, plate, is_overstay FROM spots ORDER BY id ASC")
        spots = cursor.fetchall()
        for spot in spots:
            spot['id'] = str(spot['id'])
        return spots
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/bookings/reserve")
def reserve_spot(req: BookingRequest, bg_tasks: BackgroundTasks):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("SELECT status FROM spots WHERE id = %s", (req.spot_id,))
        spot = cursor.fetchone()
        if not spot or spot["status"] != "free":
            raise HTTPException(status_code=400, detail="Spot is not available")

        cursor.execute("SELECT name, plate FROM users WHERE id = %s", (req.user_id,))
        user = cursor.fetchone()
        user_name = user["name"] if user else "Unknown"
        active_plate = req.plate or (user["plate"] if user else "SYS-TEMP")

        today = datetime.now().strftime("%Y-%m-%d")
        full_start_time = f"{today} {req.start_time}:00"


        cursor.execute(
            "INSERT INTO bookings (spot_id, user_id, start_time, plate, status) VALUES (%s, %s, %s, %s, 'Active') RETURNING *",
            (req.spot_id, req.user_id, full_start_time, active_plate)
        )
        
        cursor.execute("UPDATE spots SET status = 'occupied', plate = %s WHERE id = %s", (active_plate, req.spot_id))
        conn.commit()

        bg_tasks.add_task(manager.broadcast, {
            "type": "SPOT_UPDATE",
            "spot_id": req.spot_id,
            "updates": {"status": "occupied", "plate": active_plate}
        })
        
        new_log = {
            "id": req.spot_id,
            "plate": active_plate,
            "userName": user_name,
            "startTime": req.start_time, 
            "endTime": "Active", 
            "status": "Active"
        }
        bg_tasks.add_task(manager.broadcast, {"type": "NEW_BOOKING_LOG", "log": new_log})

        return {"message": "Booking confirmed", "booking": new_log}
        
    except Exception as e:
        conn.rollback()
        print(f"CRITICAL BOOKING ERROR: {e}") 
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
                
@app.post("/api/bookings/end")
def end_booking(req: EndBookingRequest, bg_tasks: BackgroundTasks):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # THE FIX: We added `end_time = NOW()` to this update query!
        cursor.execute(
            "UPDATE bookings SET status = 'Completed', end_time = NOW() WHERE spot_id = %s AND user_id = %s AND status = 'Active'",
            (req.spot_id, req.user_id)
        )
        cursor.execute(
            "UPDATE spots SET status = 'free', plate = NULL, is_overstay = false WHERE id = %s",
            (req.spot_id,)
        )
        conn.commit()

        bg_tasks.add_task(manager.broadcast, {
            "type": "SPOT_UPDATE",
            "spot_id": req.spot_id,
            "updates": {"status": "free", "plate": None, "isOverstay": False}
        })

        return {"message": "Booking ended successfully"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
@app.get("/api/bookings/user/{user_id}")
def get_user_bookings(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Fetch all bookings for this user, newest first
        cursor.execute("""
            SELECT spot_id, start_time, end_time, plate, status 
            FROM bookings 
            WHERE user_id = %s 
            ORDER BY start_time DESC
        """, (user_id,))
        bookings = cursor.fetchall()
        
        history = []
        active_booking = None
        
        for b in bookings:
            # Format the PostgreSQL Timestamps into clean strings for React
            booking_obj = {
                "id": b['spot_id'],
                "plate": b['plate'],
                "date": b['start_time'].strftime("%Y-%m-%d") if b['start_time'] else "",
                "startTime": b['start_time'].strftime("%H:%M") if b['start_time'] else "",
                "endTime": b['end_time'].strftime("%H:%M") if b['end_time'] else None,
                "status": b['status']
            }
            
            # Separate active bookings from finished history
            if b['status'] == 'Active':
                booking_obj['endTime'] = 'Active'
                active_booking = booking_obj
            else:
                history.append(booking_obj)
                
        return {"activeBooking": active_booking, "history": history}
    except Exception as e:
        print(f"History Fetch Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()        

# --- 7. ADMIN ENDPOINTS ---
@app.post("/api/admin/spots/{spot_id}")
def admin_update_spot(spot_id: str, req: SpotUpdateRequest, bg_tasks: BackgroundTasks):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        plate = "SYS-MANUAL" if req.status == "occupied" else None
        cursor.execute(
            "UPDATE spots SET status = %s, plate = %s, is_overstay = false WHERE id = %s",
            (req.status, plate, spot_id)
        )
        conn.commit()

        bg_tasks.add_task(manager.broadcast, {
            "type": "SPOT_UPDATE",
            "spot_id": spot_id,
            "updates": {"status": req.status, "plate": plate, "isOverstay": False}
        })

        return {"message": "Spot manually updated"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/admin/emergency")
def trigger_emergency(req: EmergencyRequest, bg_tasks: BackgroundTasks):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if req.active:
            cursor.execute("UPDATE spots SET status = 'free', plate = NULL, is_overstay = false")
            conn.commit()
        
        bg_tasks.add_task(manager.broadcast, {
            "type": "EMERGENCY",
            "active": req.active
        })
        return {"status": "emergency_triggered" if req.active else "emergency_cleared"}
    finally:
        cursor.close()
        conn.close()

# --- 8. WEBSOCKET ROUTE (This must remain async!) ---
@app.websocket("/ws/parking")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
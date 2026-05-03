# 📖 NEXUS: System Workflow & User Manual

Welcome to the Nexus Smart Parking application. This guide outlines the end-to-end user journey for standard drivers, as well as the specialized workflows for system administrators. 
URL: https://my-frontend-steel-two.vercel.app/
---

## 🚗 1. Driver Workflow: Getting Started

The primary interface is designed to be frictionless for drivers looking for immediate parking availability.

### Registration & Login
1. **Sign Up:** Users navigate to the centralized Auth Portal. Upon creating an account, the PostgreSQL database automatically assigns them the default user role (`driver`).
2. **Authentication:** Users log in using their registered email and password.
3. **Smart Routing:** The React frontend checks the user's role payload upon successful login. Because the role is `driver`, they are securely routed to the **Driver Booking Dashboard**.

### Booking a Parking Spot
1. **The Live Grid:** Upon loading the dashboard, drivers are presented with a real-time, interactive 2x5 map of the parking lot.
2. **Reading the Map:** 
   * 🟩 **Green (Free):** The spot is empty according to the AI camera. The button is clickable.
   * 🟥 **Red (Occupied):** The spot is taken (or currently being booked). The UI locks this button and disables interaction.
3. **Selection & Confirmation:** 
   * The driver clicks an available green spot (e.g., "A3").
   * The system immediately pings the backend to ensure no one else claimed it in the last millisecond (Concurrency check).
   * Upon success, the UI transitions to a confirmation screen, and spot "A3" instantly turns Red for all other users currently viewing the website.

---

## 🛡️ 2. Super Admin Workflow: The Command Center

Administrators require an unimpeded, macro-level view of the parking infrastructure. The system utilizes strict role-based access control (RBAC) to protect these routes.

### Admin Access
1. **Role Verification:** To access admin features, an account must have its role manually set to `admin_super` or `admin_staff` in the Supabase backend. Standard users cannot request this role via the UI.
2. **Secure Routing:** When an administrator logs in, the React router detects the `admin_` prefix in their role. It completely bypasses the driver map and routes them directly to the **Master Command Center** (`/admin/dashboard`).
3. **Route Protection:** If a standard driver attempts to type `/admin/dashboard` into their browser URL, the system will immediately intercept the request and redirect them back to the login screen.

### Master Command Center Features
The Admin Dashboard serves as the central hub for monitoring the AI pipeline:
* **Global Telemetry:** Admins view the real-time status of all spots simultaneously.
* **Instant Sync:** Thanks to Supabase real-time subscriptions, as the YOLOv8 camera detects cars entering or leaving spots in the physical world, the Command Center grid updates its colors live—without the admin ever needing to refresh the page.
* **Manual Override:** In the event of an AI misclassification (e.g., a camera is blocked by a tarp), Super Admins have the authority to manually click a spot on the grid and force its status to "Occupied" or "Free", instantly updating the map for all drivers.

---

## 🔄 3. How the Data Flows (The Magic Behind the UI)

For evaluators wondering how the website knows what the camera sees, here is the lifecycle of a single parking event:

1. **The Physical Event:** A physical car drives into spot B2.
2. **The Edge AI:** The YOLOv8 Python script processes the camera frame, detects the car, and calculates a >30% intersection with the geometric boundaries of spot B2.
3. **The API Ping:** The Python script sends a lightning-fast POST request to the FastAPI backend, declaring B2 as "Occupied".
4. **The Database:** FastAPI updates the PostgreSQL table in Supabase.
5. **The UI Update:** Supabase immediately blasts a real-time WebSocket update to the React frontend. Within milliseconds of the car parking, spot B2 turns Red on the mobile phones of every driver currently looking at the app.
InclusiveBridge is a premium, real-time web application designed to bridge the gap between patients (especially those with mobility or communication difficulties) and their caregivers or family members. By combining in-browser AI vision models with real-time communications, the application monitors patient safety and enables seamless interaction. 
Open your browser and navigate to "inclusivebridge.up.railway.app" to use the app.

Key Features:-

1. Real-Time AI Vision Monitoring:
Posture Detection: Monitors the patient's live camera feed using MediaPipe Pose Landmarkers to detect slouching, lateral tilts, collapses, or prolonged periods of stillness.
Facial Expression Analysis: Uses MediaPipe Face Landmarkers to read facial cues (Happy, Sad, Angry, Surprise, Neutral) to assess user distress or comfort.
Auto-Alert Engine: Triggers instant system alerts to caregivers if an abnormal posture or distress expression is sustained past a safety grace period.

2. Interactive & Assistive Communication:
Real-Time Chat: Enabled by low-latency WebSockets, allowing instant text conversations between patients and caregivers.
Speech-to-Text (STT): Integrated voice dictation allows patients to speak instead of typing.
Text-to-Speech (TTS): Reads incoming chat messages out loud automatically for patients with visual or literacy difficulties.
Voice Messages: Supports recording and playing voice messages. Audio is buffered and cached in the browser for instant playback.

3. Remote Caregiver Dashboard:
Session Controls: Patients can start a live monitoring session, generating a secure Session ID and Special Access ID (sp_id).
Remote Viewer: Caregivers can connect to the patient's live stream from any device by searching for their session and entering the secure credentials.
Historical Dashboards: At the end of each session, a summary dashboard is saved detailing total messages exchanged, system alerts triggered, and alert breakdowns.

4. Secure, Hassle-Free Authentication:
Role-Based Accounts: Simplified login/registration for Home Users (patients/family) and Professionals (caregivers/doctors).
OTP-Based Verification: Double-layer security with email One-Time Passwords (OTPs) for registration, logins, and password resets.
Smart Session Recovery: Stays logged in securely for 8 hours. Users can rejoin active sessions automatically without starting over.

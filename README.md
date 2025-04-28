# Gainesville RTS Bus Predictions App

This is a simple web and voice application for Gainesville Regional Transit System (RTS) users.  
Users can enter their bus stop ID to get real-time bus predictions via:
- Web form
- SMS text
- Voice (IVR system)

Built using **Flask**, **Twilio**, and **Python**.  
Deployed on **Render.com**.

---

## 🌐 Web App Features

- Enter a bus stop ID and receive upcoming bus predictions.
- Predictions are grouped smartly by route and destination.
- Automatic cleanup of messy user input.
- Beautiful and responsive simple design using custom CSS.

## 📱 SMS Bot Features

- Text a bus stop ID to receive bus predictions via SMS.
- Rate limiting to avoid abuse (8 messages/hour limit).

## 📞 Voice IVR Features

- Call the system and interact using keypad (DTMF) tones.
- Choose language (English / Spanish).
- Enter stop and route numbers to hear predictions.

---

## 🛠️ Setup Instructions

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/your-repo.git
   cd your-repo

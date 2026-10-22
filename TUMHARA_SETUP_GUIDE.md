# 🎯 MARK LV - Tumhare Liye Complete Setup Guide

## ✅ Pehle Check Karo - Requirements Install Hain?

```powershell
# Python version check (3.10+ chahiye)
python --version

# Dependencies install karo
python setup.py
# Ya manually:
pip install -r requirements.txt
```

---

## 🔑 Step 1: API Key Setup (Zaruri!)

1. **Gemini API Key lo** (free): https://aistudio.google.com/app/apikey
2. First run pe app khud puchega ya manually:
   - `config/api_keys.json` file banao
   ```json
   {
     "gemini_api_key": "TUMHARA_KEY_YAHA_DALO",
     "os_system": "windows",
     "assistant_name": "Jarvis",
     "user_name": "Manoj",
     "advanced_mode": true,
     "thinking_enabled": true,
     "live_screen_enabled": true,
     "proactive_audio": true
   }
   ```
   - `advanced_mode` → poore PC ka access (default ON)
   - `thinking_enabled` → soch kar jawab (default OFF)
   - `live_screen_enabled` → screen lagatar dekhe, 1 frame/sec (default OFF)
   - Language config me nahi hoti — Jarvis aapki boli se khud pehchaan kar yaad rakhta hai
   ```
   ```

---

## 🚀 Step 2: Jarvis Chalu Karo

```powershell
python main.py
```

**Pehli baar:**
- Microphone/Speaker select karo UI se
- Permission de do (camera/mic)
- "Hey Jarvis" wake word install karo (optional, UI se ek click)

---

## 🎓 Step 3: App Workflow Sikhao (App Tutor)

### Kaise Sikhaye:
```
Tum (voice se): "Jarvis, HUL Driver Hub app me bill entry ka tareeka sikha raha hoon:
1. Pehle Payments tab click karo
2. Phir New Entry button
3. Bill Number field me number type karo
4. Amount field me amount daalo
5. Submit par click karo"

Jarvis: [Silently save karega memory/app_steps/hul_driver_hub.json me]
```

### Next Time Automatic:
```
Tum: "Jarvis, HUL app me bill 12345 ko Rs 5000 entry karo"

Jarvis: [Pehle app_tutor recall karega saved steps → phir execute karega]
```

**File location**: `memory/app_steps/<app_name>.json`
- Manually edit bhi kar sakte ho
- Purane versions history me save rehte hain

---

## 🖼️ Step 4: Image Scanning & OCR

### Method 1: Voice Command
```
Tum: "Jarvis, screenshot dekho aur text extract karo"
Jarvis: [Camera/screen capture → Gemini OCR]
```

### Method 2: File Upload (agar UI drag-drop support karta hai)
```
Tum: "Is image me kya likha hai?"
Jarvis: file_processor OCR karega
```

### Manual Way:
```python
# Ya directly file path de do
"Jarvis, D:\screenshots\payment.png me text read karo"
```

---

## 📧 Step 4.5: Email Setup (CV Attach Karke Bhejna)

`actions/email_sender.py` ab Jarvis mein hai. Ek baar config karo:

```json
// config/api_keys.json me add karo
{
  "email": {
    "provider": "gmail",
    "address": "tumhara@gmail.com",
    "password": "16-char App Password"
  }
}
```

**Gmail App Password kaise lo:**
1. Google Account → Security → 2-Step Verification ON karo
2. myaccount.google.com/apppasswords → naya App Password banao
3. Wahi 16-character password upar dalo (normal Gmail password kaam NAHI karega)

**Commands:**
```
"Jarvis, hr@company.com ko email bhejo — subject 'Application for Driver',
 CV attach karo D:\Documents\Manoj_CV.pdf se"
"Jarvis, mere naye email padho"
"Jarvis, check karo hr@company.com se koi mail aaya?"
```

---

## 💬 Step 4.6: WhatsApp Verified Driver (Ab Kaam Karega)

Pehle WhatsApp bhejna **blind keystrokes** pe chalta tha (galat chat me ja sakta tha).
Ab `plugins/_whatsapp_core.py` driver hai jo:
- Real WhatsApp window dhundhta hai
- Conversation kholne ke baad **header verify** karta hai ki sahi insaan ki chat khuli hai
- Type karne ke baad **verify** karta hai message box me pada hai
- Enter ke baad **confirm** karta hai box khaali ho gaya = sent

```
"Jarvis, WhatsApp pe Riya ko bolo — payment aaj shaam tak aayega"
"Jarvis, WhatsApp pe Riya ki last messages padh ke bata"
```

**Bill matching:**
```
"Jarvis, Online Pay Confinace group scan karo"
"Jarvis, bill 12345 ka payment aaya? kitne ka?"
```

---

## 📱 Step 4.7: Mobile se PC Control (Same WiFi)

Dashboard pehle se bana hai (`dashboard/server.py`):
1. UI me **Remote Control** → QR code scan karo phone se
2. Phone se text command bhejo — PC pe Jarvis wake hokar execute karega
3. Phone ka mic bhi kaam karta hai (`/ws/phone-audio`)
4. File upload bhi (`/api/upload`)

---

## 🎯 Step 5: Full System Access Enable Karo

`system_access.py` already enabled hai by default. Check:

```json
// config/api_keys.json me
{
  "advanced_mode": true  // Yeh ON hona chahiye
}
```

Ab Jarvis yeh sab kar sakta hai:
- ✅ Kisi bhi drive pe access (C:, D:, E:)
- ✅ Program Files, System32, kahi bhi file operations
- ✅ Commands run karna (cmd, powershell)
- ✅ Processes manage (start/stop)
- ✅ Apni khud ki code fix karna (`self_fix`, `self_test`)

---

## 🧠 Step 6: Thinking Mode Enable Karo

Gemini thinking (chain-of-thought) ke liye:

```json
// config/api_keys.json me add karo
{
  "thinking_enabled": true
}
```

Restart karo, ab Jarvis complex tasks pe reasoning karega.

---

## 🎨 Step 7: UI Customize Karo

App kholo → ⚙️ SETUP button:
- **Assistant Name**: "Jarvis" → "Kuch bhi"
- **Your Name**: "Manoj" ya apna naam
- **Voice**: 5 Gemini voices me se choose karo
- **Theme Color**: Hue wheel se color change karo
- **Wake Word**: "Hey Jarvis" enable/disable
- **Push-to-Talk**: Ctrl+Space (global hotkey)

---

## 📱 Step 8: Remote Control (Phone Se)

1. UI me **Remote Control** button click karo
2. QR code scan karo phone se
3. Ab phone se control kar sakte ho

Manual entry: `https://192.168.1.68:8000` (apna IP use karo)

---

## 🔥 Real Use Cases (Tumhare Kaam Ke)

### Use Case 1: WhatsApp Payment Matching
```
Setup:
1. WhatsApp screenshots folder set karo
2. HUL Driver app workflow sikha do (Steps 3 dekho)

Commands:
"Jarvis, WhatsApp payments folder scan karo"
"Jarvis, har screenshot me bill number extract karo"
"Jarvis, HUL app me ye bill check karo aur match karo"
"Agar match nahi to mujhe bata do differences"
```

### Use Case 2: Automated Data Entry
```
"Jarvis, Excel sheet D:\bills.xlsx kholo"
"Jarvis, pehli row se bill numbers padho"
"Jarvis, har bill HUL app me enter karo saved steps se"
```

### Use Case 3: Screen Monitoring
```
"Jarvis, screen dekho aur bata do kya ho raha hai"
"Jarvis, har 5 minute me screen check karo aur notify karo"
```

### Use Case 4: Self-Repair
```
Agar Jarvis me error aaye:
"Jarvis, apne aap ko test karo"  → self_test() chalega
"Jarvis, error fix karo"         → self_fix() automatic repair karega
```

---

## 🛠️ Troubleshooting

### Problem: "Gemini out of quota"
**Solution**: 
- Free API key daily limit hit ho gayi
- Wait karo 24 hours ya paid plan lo
- Ya ladder automatically fallback karega slower models pe

### Problem: "Camera not working"
**Solution**:
```powershell
pip install opencv-python
# Config me camera index set karo (usually 0 ya 1)
```

### Problem: "Screen capture failed"
**Solution**:
```powershell
pip install mss pillow pyautogui
```

### Problem: "App workflow nahi chal raha"
**Solution**:
- Check `memory/app_steps/<app_name>.json` file exists
- Manually edit karke test karo
- Steps clear aur detailed likho

---

## 🎯 Advanced: Custom Plugin Banao

Agar koi specific feature chahiye:

1. Copy karo: `plugins/_template.py`
2. Rename karo: `plugins/mera_feature.py`
3. Edit karo PLUGIN dict:
```python
PLUGIN = {
    "name": "mera_feature",
    "description": "Kya karta hai yeh",
    "parameters": {...}
}

def run(parameters, player=None, session_memory=None):
    # Tumhara code yaha
    return "Result"
```
4. Restart karo Jarvis - automatically load ho jayega

---

## 📊 Monitoring & Logs

**Logs check karo**:
- `run_stdout.log` - Normal operations
- `run_stderr.log` - Errors
- `memory/long_term.json` - Stored memories
- `memory/app_steps/` - Saved workflows

**Live monitoring**:
UI me activity log realtime dikhta hai

---

## 🚀 Pro Tips

1. **Silent Mode**: "Chup reh, kaam karte reh" → Jarvis silently kaam karega
2. **Undo**: "Jarvis, undo" → Last action reverse hoga
3. **Multi-step**: "Jarvis, pehle X karo phir Y karo" → Order me execute karega
4. **Confirmation**: Shutdown/WiFi toggle pe button aayega confirm karne ko
5. **Memory Panel**: UI se dekho kya kya yaad hai, delete bhi kar sakte ho

---

## 🎓 Next Level: Self-Learning Bot

Jarvis ko truly emotional aur self-improving bane ke liye:

1. **Proactive Mode** already hai - time-aware check-ins
2. **Memory** automatic save hota hai - preferences, context
3. **Self-fix** already implemented - errors khud thik karta hai
4. **Agent Planner** - complex multi-step tasks automatically plan + execute

**Missing (agar chahiye to add karna hoga):**
- Sentiment analysis on your voice tone
- Facial expression recognition from camera
- Personality customization (humor, formality level)
- Learning from corrections (already partial via memory)

---

## 📞 Help & Support

- **Readme**: `readme.md` me detailed features list hai
- **Tests**: `tests/` folder me examples
- **Code**: Har file me detailed comments hain
- **YouTube**: [@FatihMakes](https://www.youtube.com/@FatihMakes) - Creator ka channel

---

## ✅ Final Checklist

- [ ] Python 3.10+ installed
- [ ] Dependencies installed (`python setup.py`)
- [ ] Gemini API key config me dala
- [ ] Microphone/speaker configured
- [ ] App workflows sikha diye (app_tutor)
- [ ] Advanced mode enabled (system_access)
- [ ] Test run - "Hey Jarvis" ya Ctrl+Space

**Ab tum ready ho! Jarvis tumhara full digital assistant hai.** 🚀

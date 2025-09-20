# ai-complaint-chatbot
# AI Complaint Management Chatbot

An intelligent, multilingual chatbot that accepts user complaints, predicts the correct category and sub-category using machine-learning models, asks follow-up questions for missing details, and files the complaint automatically.  
Built as part of a self-learning project to streamline complaint handling for platforms similar to SEBI’s SCORES system.

---

## 🚀 Features
- Accepts **free-text complaints** and optional **file attachments** (PDF, image, DOCX).
- Predicts **category and sub-category** using Logistic Regression with TF-IDF vectorization.
- Asks for **confirmation and edits** before lodging the complaint.
- Generates a **unique complaint number** and stores details in a SQLite database.
- Provides **multilingual support** (all major Indian languages + Hinglish) with automatic language detection and translation.
- Modern **WhatsApp-style UI** with:
  - Typing indicator  
  - File preview with cancel option  
  - Voice-to-text input

---

## 🛠️ Tech Stack
- **Backend:** Python, Flask
- **Machine Learning:** scikit-learn (Logistic Regression, TF-IDF)
- **Database:** SQLite
- **Frontend:** HTML, CSS, JavaScript (chat bubble UI)
- **Other Libraries:** googletrans (for language detection & translation)

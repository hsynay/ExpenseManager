# 🏗️ Construction Project Management System

A modern and user-friendly web-based application to manage construction projects, customer payments, expense tracking, and financial summaries. This system helps construction companies track apartment sales, manage cooperative or standard project types, and monitor cash flow with clarity and ease.

<div align="center">

![Dashboard Preview](SS_dashboard.png)

---

![Detail of Debts Preview](SS_debts.png)

---

![Detail of Reports Preview](SS_reports.png)


[![Live Demo](https://img.shields.io/badge/🌐_Live_Demo-Render-blue?style=for-the-badge)](https://construction-project-i03m.onrender.com)
[![Made with Flask](https://img.shields.io/badge/Made_with-Flask-green?style=for-the-badge&logo=flask)](https://flask.palletsprojects.com/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-blue?style=for-the-badge&logo=postgresql)](https://www.postgresql.org/)

</div>

---

## 🚀 Live Demo

🔗 **[View Application](https://construction-project-i03m.onrender.com)**  
> _Note: It may take a few seconds to wake up if the service is idle._

---

## 🛠️ Technologies

<table>
<tr>
<td>

**Backend**
- ![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white) Flask
- ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat&logo=postgresql&logoColor=white) Database
- ![Werkzeug](https://img.shields.io/badge/Security-Werkzeug-red?style=flat) Security

</td>
<td>

**Frontend**
- ![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=flat&logo=html5&logoColor=white) 
- ![Bootstrap](https://img.shields.io/badge/Bootstrap-563D7C?style=flat&logo=bootstrap&logoColor=white) CSS Framework
- ![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=flat&logo=javascript&logoColor=black)
- ![Chart.js](https://img.shields.io/badge/Chart.js-FF6384?style=flat&logo=chart.js&logoColor=white)

</td>
</tr>
</table>

**Deployment & Tools**
- 🚀 **Render.com** - Cloud Deployment
- 🔒 **Werkzeug Security** - Password Hashing & Authentication
- 📊 **Chart.js** - Interactive Charts
- 🗄️ **psycopg2** - PostgreSQL Adapter

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 👤 User Management
- 🔐 Secure login system
- 🔑 Encrypted password storage
- 👥 Multi-user support

### 📁 Project Management
- 🏢 Normal & Cooperative project types
- 🏠 Apartment allocation & floor/block tracking
- 📋 Project status monitoring

### 💰 Financial Tracking
- 💸 Customer payment tracking
- 📅 Manual or scheduled installments
- 💳 Cash & check management

</td>
<td width="50%">

### 📊 Reporting & Analytics
- 📈 Monthly financial overview
- 📊 Income & expense charts
- 📋 Detailed payment reports

### ⚠️ Alert System
- 🔔 Overdue payments
- ⏰ Upcoming payment reminders
- 📢 Automatic notifications

### 🛠️ Admin Panel
- 👥 Customer management
- 🏗️ Project management
- 🏪 Supplier management

</td>
</tr>
</table>

---

## 🚀 Installation & Setup

### Prerequisites
- Python 3.8+
- PostgreSQL
- Git

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/hsynay/konak.git
cd konak
```

### 2️⃣ Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python -m venv venv
source venv/bin/activate
```

### 3️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

### 4️⃣ Configure Environment Variables

```bash
# Create .env file
cp .env.example .env

# Edit .env file
DATABASE_URL=postgresql://username:password@localhost/dbname
SECRET_KEY=your_secret_key_here
```

### 5️⃣ Run the Application

```bash
flask run
```

🎉 The application will start running at `http://localhost:5000`!

---

## 📸 Screenshots

<details>
<summary>📊 Dashboard</summary>

- Project summaries
- Financial charts
- Quick access menus

</details>

<details>
<summary>💰 Payment Tracking</summary>

- Customer debt status
- Installment plans
- Payment history

</details>

<details>
<summary>📈 Reports</summary>

- Monthly income-expense reports
- Project-based analysis
- Check tracking system

</details>

---

## 🗂️ Project Structure

```
odeme-takip/
├── app.py                 # Main Flask application
├── templates/            # HTML templates
│   ├── dashboard.html
│   ├── debts.html
│   └── ...
├── static/              # CSS, JS, images
├── requirements.txt     # Python dependencies
├── .env.example        # Environment variables template
└── README.md
```

---

## 🔧 Configuration

Create a `.env` file in the root directory:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/dbname
SECRET_KEY=your-secret-key-here
FLASK_ENV=development
```

---

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. Fork this repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📞 Contact & Support

<div align="center">

**🧑‍💻 Developer: Hüseyin Ay**

[![GitHub](https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white)](https://github.com/hsynay)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/h%C3%BCseyin-ay01)

📧 **Email:** huseyin_1153@hotmail.com  

</div>

---

<div align="center">

**⭐ If you like this project, don't forget to give it a star**

# 📜 Legalitik Live Agent Backend API

A RESTful API built with **Node.js, Express, and MySQL** for handling user authentication (Registration and Login) on the Legalitik Live Agent platform. Secure password hashing is managed via **Bcrypt**.

---

# ⚙️ Setup

Install Node.js dependencies:

```bash
npm install
```

Configure the MySQL database by creating a database named `hackathon`, then run the following SQL command to create the required table:

```sql
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    akun VARCHAR(50) NOT NULL,
    is_online TINYINT(1) DEFAULT 0,
    last_active DATETIME DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

# 🔑 Environment Variables

Create a `.env` file in the root directory:

```env
PORT=3000

DB_HOST=localhost
DB_USER=root
DB_PASSWORD=
DB_NAME=hackathon
```

---

# 🚀 Usage

To start the backend server, simply run:

```bash
node index.js
```

The API will be available at `http://localhost:3000`.

---

# 🧩 How It Works

The API consists of two main authentication endpoints.

## 📝 POST /api/register

- Validates incoming user data (name, email, password, accountType)
- Checks if the email is already registered in the database
- Hashes the password using **bcrypt**
- Stores the new user record in the database

## 🔐 POST /api/login

- Verifies user email existence
- Compares the provided password against the hashed password
- **Auto-fixes legacy passwords**: If a user logs in with an old plain-text password, it seamlessly hashes and updates it in the database
- Updates the user's `is_online` status and `last_active` timestamp

```text
client request → validate data → hash/compare password → update database → return JSON response
```

---

# 🗄️ Database Structure

| Column | Type | Description |
|------|-------------|-------------|
| `id` | `INT` | Primary Key, Auto Increment |
| `name` | `VARCHAR(255)` | User's full name |
| `email` | `VARCHAR(255)` | User's email address (Unique) |
| `password` | `VARCHAR(255)` | Bcrypt hashed password |
| `akun` | `VARCHAR(50)` | Account type (e.g., bkn, bmkg, adsqoo) |
| `is_online` | `TINYINT(1)` | Active session flag |

---

# 📦 API Response Example

**Login Success Response:**

```json
{
  "success": true,
  "message": "Login Berhasil!",
  "token": "mock_token_1",
  "user": {
    "id": 1,
    "name": "John Doe",
    "email": "johndoe@email.com",
    "accountType": "bkn"
  }
}
```

---

# 📊 Console Output Example

```text
Server berjalan di http://localhost:3000

User Baru Terdaftar (Encrypted): johndoe@email.com
```

---

# 🧠 Notes

- Leave `DB_PASSWORD` empty in the `.env` file if you are using a default XAMPP/WAMP setup.
- The `.env` file must be added to your `.gitignore` to prevent database credentials from leaking into public repositories.
- The login endpoint returns a `mock_token`. For production, this should be replaced with an actual JWT (JSON Web Token) implementation.
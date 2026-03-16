const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const mysql = require('mysql2/promise');
const bcrypt = require('bcryptjs');

const app = express();
const PORT = 3000;

app.use(cors());
app.use(bodyParser.json());

// --- KONFIGURASI DATABASE ---
const dbConfig = {
    host: 'localhost',
    user: 'root',
    password: '',
    database: 'tendr'
};

async function getConnection() {
    return await mysql.createConnection(dbConfig);
}

// --- ROUTE REGISTER ---
app.post('/api/register', async (req, res) => {
    const { name, email, password, accountType } = req.body;

    if (!name || !email || !password || !accountType) {
        return res.status(400).json({ success: false, message: "Semua field wajib diisi!" });
    }

    let connection;
    try {
        connection = await getConnection();
        
        // 1. Cek email
        const [existingUsers] = await connection.execute(
            'SELECT * FROM users WHERE email = ?', 
            [email]
        );

        if (existingUsers.length > 0) {
            return res.status(400).json({ success: false, message: "Email sudah terdaftar!" });
        }

        // 2. ENKRIPSI PASSWORD (HASHING)
        const salt = await bcrypt.genSalt(10);
        const hashedPassword = await bcrypt.hash(password, salt);

        // 3. Simpan password yang sudah di-hash
        const [result] = await connection.execute(
            'INSERT INTO users (name, email, password, akun, created_at) VALUES (?, ?, ?, ?, NOW())',
            [name, email, hashedPassword, accountType]
        );

        console.log("User Baru Terdaftar (Encrypted):", email);
        
        res.status(201).json({
            success: true,
            message: "Registrasi berhasil"
        });

    } catch (error) {
        console.error("Error Register:", error);
        res.status(500).json({ success: false, message: "Error database" });
    } finally {
        if (connection) await connection.end();
    }
});

// --- ROUTE LOGIN ---
app.post('/api/login', async (req, res) => {
    const { email, password } = req.body;

    let connection;
    try {
        connection = await getConnection();

        // 1. Ambil user berdasarkan Email
        const [rows] = await connection.execute(
            'SELECT * FROM users WHERE email = ?',
            [email]
        );

        if (rows.length === 0) {
            return res.status(401).json({ success: false, message: "Email tidak ditemukan!" });
        }

        const user = rows[0];
        let isPasswordValid = false;
        let needsRehash = false;

        // 2. Cek Password
        if (user.password.startsWith('$2y$') || user.password.startsWith('$2a$') || user.password.startsWith('$2b$')) {
            isPasswordValid = await bcrypt.compare(password, user.password);
        } else {
            // Password polos (legacy)
            if (user.password === password) {
                isPasswordValid = true;
                needsRehash = true;
            }
        }

        if (isPasswordValid) {
            // 3. Auto-fix password legacy
            if (needsRehash) {
                const salt = await bcrypt.genSalt(10);
                const newHash = await bcrypt.hash(password, salt);
                await connection.execute('UPDATE users SET password = ? WHERE id = ?', [newHash, user.id]);
            }

            // Update status online
            await connection.execute('UPDATE users SET is_online = 1, last_active = NOW() WHERE id = ?', [user.id]);

            res.status(200).json({
                success: true,
                message: "Login Berhasil!",
                token: "mock_token_" + user.id,
                user: {
                    id: user.id,
                    name: user.name,
                    email: user.email,
                    accountType: user.akun
                }
            });
        } else {
            res.status(401).json({ success: false, message: "Password salah!" });
        }

    } catch (error) {
        console.error("Error Login:", error);
        res.status(500).json({ success: false, message: "Error server" });
    } finally {
        if (connection) await connection.end();
    }
});

// --- ROUTE LOGOUT (BARU) ---
app.post('/api/logout', async (req, res) => {
    const { userId } = req.body;

    // Jika userId tidak dikirim, tetap anggap sukses (clear session di frontend)
    if (!userId) {
        return res.status(200).json({ success: true, message: "Logout frontend only" });
    }

    let connection;
    try {
        connection = await getConnection();
        // Update status menjadi offline (0)
        await connection.execute('UPDATE users SET is_online = 0 WHERE id = ?', [userId]);
        
        console.log(`User ID ${userId} logout.`);
        res.status(200).json({ success: true, message: "Logout berhasil" });
    } catch (error) {
        console.error("Error Logout:", error);
        res.status(500).json({ success: false, message: "Error server" });
    } finally {
        if (connection) await connection.end();
    }
});

app.listen(PORT, () => {
    console.log(`Server berjalan di http://localhost:${PORT}`);
});
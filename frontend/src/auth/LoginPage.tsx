import { useState } from 'react';
import type { FormEvent, ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom'; 
import LogoKatalitix from '../assets/Property 1=Variant3.png';

// --- Tipe Data ---
interface LoginFormData {
  email: string;
  password: string;
}

interface RegisterFormData {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
  accountType: string;
}

type ViewState = 'login' | 'register';

const AuthPage = () => {
  // --- State Management ---
  const [view, setView] = useState<ViewState>('login');
  const navigate = useNavigate(); // Hook untuk navigasi

  // State untuk visibilitas password
  const [showLoginPass, setShowLoginPass] = useState(false);
  const [showRegPass, setShowRegPass] = useState(false);
  const [showRegConfirmPass, setShowRegConfirmPass] = useState(false);

  // State untuk loading dan error
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  // State untuk data form
  const [loginData, setLoginData] = useState<LoginFormData>({
    email: '',
    password: ''
  });

  const [registerData, setRegisterData] = useState<RegisterFormData>({
    name: '',
    email: '',
    password: '',
    confirmPassword: '',
    accountType: ''
  });

  // --- Handlers ---
  const handleLoginChange = (e: ChangeEvent<HTMLInputElement>) => {
    setLoginData({ ...loginData, [e.target.name]: e.target.value });
  };

  const handleRegisterChange = (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setRegisterData({ ...registerData, [e.target.name]: e.target.value });
  };

  // --- LOGIKA LOGIN ---
  const handleLoginSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMessage('');

    try {
      const response = await fetch('http://localhost:3000/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: loginData.email,
          password: loginData.password
        }),
      });

      const data = await response.json();

      if (response.ok) {
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify(data.user));
        navigate('/'); 
      } else {
        setErrorMessage(data.message || 'Login gagal');
      }
    } catch (error) {
      console.error('Login Error:', error);
      setErrorMessage('Gagal terhubung ke server.');
    } finally {
      setIsLoading(false);
    }
  };

  // --- LOGIKA REGISTER ---
  const handleRegisterSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMessage('');

    if (registerData.password !== registerData.confirmPassword) {
      setErrorMessage('Password konfirmasi tidak cocok!');
      return;
    }

    setIsLoading(true);

    try {
      const response = await fetch('http://localhost:3000/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: registerData.name,
          email: registerData.email,
          password: registerData.password,
          accountType: registerData.accountType
        }),
      });

      const data = await response.json();

      if (response.ok) {
        alert('Registrasi Berhasil! Silakan login.');
        setView('login'); 
        setRegisterData({ name: '', email: '', password: '', confirmPassword: '', accountType: '' });
      } else {
        setErrorMessage(data.message || 'Registrasi gagal');
      }
    } catch (error) {
      console.error('Register Error:', error);
      setErrorMessage('Gagal terhubung ke server.');
    } finally {
      setIsLoading(false);
    }
  };

  const inputClassName = `
    w-full p-2.5 pl-10 
    border border-gray-300 rounded-lg 
    bg-gray-50 text-gray-900
    transition-colors duration-200 ease-in-out
    focus:outline-none focus:border-red-600 focus:ring-2 focus:ring-red-600/20
  `;

  const labelClassName = "block text-sm font-medium text-gray-700 mb-1";

  return (
    <div className="bg-gray-50 flex items-center justify-center min-h-screen p-4 font-sans">
      <div className="w-full max-w-4xl">
        <div className="bg-white rounded-2xl shadow-lg border border-gray-200/80 flex flex-col md:flex-row overflow-hidden min-h-[600px]">

          {/* --- Kolom Kiri (Branding) --- */}
          <div className="w-full md:w-1/2 bg-red-700 text-white flex flex-col justify-center p-8 sm:p-12">
            <div className="relative w-full h-full flex flex-col justify-center items-center md:items-start">
              <a href="#" className="mb-8 block">
                <img 
                  src={LogoKatalitix}
                  alt="Katalitix Logo" 
                  className="w-auto h-auto object-contain"
                  onError={(e) => {
                     e.currentTarget.style.display = 'none';
                  }}
                />
              </a>
            </div>
          </div>

          {/* --- Kolom Kanan (Form Dinamis) --- */}
          <div className="w-full md:w-1/2 p-8 sm:p-12 flex flex-col justify-center">
            
            {errorMessage && (
              <div className="mb-4 p-3 bg-red-100 border border-red-400 text-red-700 rounded text-sm">
                {errorMessage}
              </div>
            )}

            {/* === LOGIN VIEW === */}
            {view === 'login' && (
              <div className="animate-fade-in">
                <h2 className="text-2xl font-bold text-gray-800 mb-2">Selamat Datang Kembali</h2>
                <p className="text-gray-500 mb-8">Silakan masukkan kredensial Anda untuk melanjutkan.</p>

                <form onSubmit={handleLoginSubmit} className="space-y-6">
                  <div>
                    <label htmlFor="login-email" className={labelClassName}>Alamat Email</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                          <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                          <polyline points="22,6 12,13 2,6"></polyline>
                        </svg>
                      </span>
                      <input
                        type="email"
                        name="email"
                        id="login-email"
                        className={inputClassName}
                        placeholder="anda@email.com"
                        value={loginData.email}
                        onChange={handleLoginChange}
                        required
                      />
                    </div>
                  </div>

                  <div>
                    <div className="flex justify-between items-center mb-1">
                      <label htmlFor="login-password" className={labelClassName}>Password</label>
                      <a href="#" className="text-sm text-red-600 hover:underline font-medium">Lupa Password?</a>
                    </div>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                          <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                        </svg>
                      </span>
                      <input
                        type={showLoginPass ? "text" : "password"}
                        name="password"
                        id="login-password"
                        className={`${inputClassName} pr-10`}
                        placeholder="••••••••"
                        value={loginData.password}
                        onChange={handleLoginChange}
                        required
                      />
                      <button
                        type="button"
                        className="absolute inset-y-0 right-0 flex items-center pr-3 focus:outline-none text-gray-400 hover:text-gray-600"
                        onClick={() => setShowLoginPass(!showLoginPass)}
                      >
                        {showLoginPass ? (
                           <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                             <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-10-7-10-7a1.84 1.84 0 0 1 2.5-1.07M22 12s-3 7-10 7c-1.05 0-2.06-.18-3-.5M5.94 5.94A10.07 10.07 0 0 1 12 4c7 0 10 7 10 7a1.84 1.84 0 0 1-2.5 1.07"></path>
                             <line x1="1" y1="1" x2="23" y2="23"></line>
                           </svg>
                        ) : (
                          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                            <path d="M2 12s5 7 10 7 10-7 10-7-5-7-10-7-10 7-10 7Z"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                          </svg>
                        )}
                      </button>
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={isLoading}
                    className={`w-full px-4 py-3 text-sm font-semibold text-white bg-red-600 rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 transition-all duration-300 ease-in-out transform hover:scale-105 ${isLoading ? 'opacity-70 cursor-not-allowed' : ''}`}
                  >
                    {isLoading ? 'Memproses...' : 'Masuk'}
                  </button>
                </form>

                <div className="mt-8 text-center">
                  <p className="text-sm text-gray-600">
                    Belum punya akun?{' '}
                    <button onClick={() => setView('register')} className="font-semibold text-red-600 hover:underline">
                      Daftar sekarang
                    </button>
                  </p>
                </div>
              </div>
            )}

            {/* === REGISTER VIEW === */}
            {view === 'register' && (
              <div className="animate-fade-in">
                <h2 className="text-2xl font-bold text-gray-800 mb-2">Buat Akun Baru</h2>
                <p className="text-gray-500 mb-8">Mulai perjalanan Anda bersama Katalitiʞ.</p>

                <form onSubmit={handleRegisterSubmit} className="space-y-4">
                  <div>
                    <label htmlFor="name" className={labelClassName}>Nama Lengkap</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                          <circle cx="12" cy="7" r="4"></circle>
                        </svg>
                      </span>
                      <input
                        type="text"
                        name="name"
                        id="name"
                        className={inputClassName}
                        placeholder="Nama Anda"
                        value={registerData.name}
                        onChange={handleRegisterChange}
                        required
                      />
                    </div>
                  </div>

                  <div>
                    <label htmlFor="register-email" className={labelClassName}>Email</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                          <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                          <polyline points="22,6 12,13 2,6"></polyline>
                        </svg>
                      </span>
                      <input
                        type="email"
                        name="email"
                        id="register-email"
                        className={inputClassName}
                        placeholder="anda@email.com"
                        value={registerData.email}
                        onChange={handleRegisterChange}
                        required
                      />
                    </div>
                  </div>

                  <div>
                    <label htmlFor="register-password" className={labelClassName}>Password</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                          <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                        </svg>
                      </span>
                      <input
                        type={showRegPass ? "text" : "password"}
                        name="password"
                        id="register-password"
                        className={`${inputClassName} pr-10`}
                        placeholder="••••••••"
                        value={registerData.password}
                        onChange={handleRegisterChange}
                        required
                      />
                      <button
                        type="button"
                        className="absolute inset-y-0 right-0 flex items-center pr-3 focus:outline-none text-gray-400 hover:text-gray-600"
                        onClick={() => setShowRegPass(!showRegPass)}
                      >
                        {showRegPass ? (
                           <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                             <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-10-7-10-7a1.84 1.84 0 0 1 2.5-1.07M22 12s-3 7-10 7c-1.05 0-2.06-.18-3-.5M5.94 5.94A10.07 10.07 0 0 1 12 4c7 0 10 7 10 7a1.84 1.84 0 0 1-2.5 1.07"></path>
                             <line x1="1" y1="1" x2="23" y2="23"></line>
                           </svg>
                        ) : (
                          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                            <path d="M2 12s5 7 10 7 10-7 10-7-5-7-10-7-10 7-10 7Z"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                          </svg>
                        )}
                      </button>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="confirm_password" className={labelClassName}>Konfirmasi Password</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                          <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                        </svg>
                      </span>
                      <input
                        type={showRegConfirmPass ? "text" : "password"}
                        name="confirmPassword"
                        id="confirm_password"
                        className={`${inputClassName} pr-10`}
                        placeholder="••••••••"
                        value={registerData.confirmPassword}
                        onChange={handleRegisterChange}
                        required
                      />
                      <button
                        type="button"
                        className="absolute inset-y-0 right-0 flex items-center pr-3 focus:outline-none text-gray-400 hover:text-gray-600"
                        onClick={() => setShowRegConfirmPass(!showRegConfirmPass)}
                      >
                          {showRegConfirmPass ? (
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                              <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-10-7-10-7a1.84 1.84 0 0 1 2.5-1.07M22 12s-3 7-10 7c-1.05 0-2.06-.18-3-.5M5.94 5.94A10.07 10.07 0 0 1 12 4c7 0 10 7 10 7a1.84 1.84 0 0 1-2.5 1.07"></path>
                              <line x1="1" y1="1" x2="23" y2="23"></line>
                            </svg>
                          ) : (
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                              <path d="M2 12s5 7 10 7 10-7 10-7-5-7-10-7-10 7-10 7Z"></path>
                              <circle cx="12" cy="12" r="3"></circle>
                            </svg>
                          )}
                      </button>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="akun" className={labelClassName}>Pilih Tipe Akun Admin</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none text-gray-400">
                          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
                      </span>
                      <select
                        id="akun"
                        name="accountType"
                        className={`${inputClassName} appearance-none`}
                        value={registerData.accountType}
                        onChange={handleRegisterChange}
                        required
                      >
                        <option value="" disabled>Pilih Tipe Akun</option>
                        <option value="bkn">BKN</option>
                        <option value="adsqoo">Adsqoo</option>
                        <option value="bmkg">BMKG</option>
                        <option value="optimis">Optimis Kab Bogor</option>
                        <option value="disdik">Disdik SPMB</option>
                      </select>
                      <span className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none text-gray-400">
                          <svg className="h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" /></svg>
                      </span>
                    </div>
                  </div>

                  <div>
                    <button
                      type="submit"
                      disabled={isLoading}
                      className={`w-full px-4 py-3 mt-2 text-sm font-semibold text-white bg-red-600 rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 transition-all duration-300 ease-in-out transform hover:scale-105 ${isLoading ? 'opacity-70 cursor-not-allowed' : ''}`}
                    >
                      {isLoading ? 'Mendaftarkan...' : 'Daftar'}
                    </button>
                  </div>
                </form>

                <div className="mt-8 text-center">
                  <p className="text-sm text-gray-600">
                    Sudah punya akun?{' '}
                    <button onClick={() => setView('login')} className="font-semibold text-red-600 hover:underline">
                      Masuk di sini
                    </button>
                  </p>
                </div>
              </div>
            )}

          </div>
        </div>
      </div>
    </div>
  );
};

export default AuthPage;
import { useState } from 'react';
import type { FormEvent, ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom'; 
import LogoKatalitix from '../assets/Property 1=Variant3.png';

// --- Data Types ---
interface LoginFormData {
  email: string;
  password: string;
}

const AuthPage = () => {
  // --- State Management ---
  const navigate = useNavigate(); 

  // Password visibility state
  const [showPassword, setShowPassword] = useState(false);

  // Loading and error state
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  // Form data state
  const [loginData, setLoginData] = useState<LoginFormData>({
    email: '',
    password: ''
  });

  // --- Handlers ---
  const handleLoginChange = (e: ChangeEvent<HTMLInputElement>) => {
    setLoginData({ ...loginData, [e.target.name]: e.target.value });
  };

  // --- LOGIN LOGIC ---
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
        setErrorMessage(data.message || 'Login failed');
      }
    } catch (error) {
      console.error('Login Error:', error);
      setErrorMessage('Failed to connect to the server.');
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

          {/* --- Left Column (Branding) --- */}
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

          {/* --- Right Column (Form) --- */}
          <div className="w-full md:w-1/2 p-8 sm:p-12 flex flex-col justify-center">
            
            {errorMessage && (
              <div className="mb-4 p-3 bg-red-100 border border-red-400 text-red-700 rounded text-sm">
                {errorMessage}
              </div>
            )}

            {/* === LOGIN VIEW === */}
            <div className="animate-fade-in">
              <h2 className="text-2xl font-bold text-gray-800 mb-2">Welcome Back</h2>
              <p className="text-gray-500 mb-8">Please enter your credentials to continue.</p>

              <form onSubmit={handleLoginSubmit} className="space-y-6">
                <div>
                  <label htmlFor="login-email" className={labelClassName}>Email Address</label>
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
                      placeholder="you@email.com"
                      value={loginData.email}
                      onChange={handleLoginChange}
                      required
                    />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label htmlFor="login-password" className={labelClassName}>Password</label>
                    <a href="#" className="text-sm text-red-600 hover:underline font-medium">Forgot Password?</a>
                  </div>
                  <div className="relative">
                    <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
                      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                      </svg>
                    </span>
                    <input
                      type={showPassword ? "text" : "password"}
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
                      onClick={() => setShowPassword(!showPassword)}
                    >
                      {showPassword ? (
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
                  {isLoading ? 'Processing...' : 'Sign In'}
                </button>
              </form>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

export default AuthPage;
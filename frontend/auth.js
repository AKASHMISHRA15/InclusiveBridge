let currentMode = "login";

function isValidEmail(email) {
    const re = /^[\w\.-]+@[\w\.-]+\.\w+$/;
    return re.test(email);
}

function showRegister() {
    document.getElementById("login-form").style.display = "none";
    document.getElementById("register-form").style.display = "block";
    document.getElementById("otp-form").style.display = "none";
    document.getElementById("form-title").innerText = "Register";
    currentMode = "register";
    setRole("home");
}

function showLogin() {
    document.getElementById("register-form").style.display = "none";
    document.getElementById("login-form").style.display = "block";
    document.getElementById("otp-form").style.display = "none";
    const fpForm = document.getElementById("forgot-password-form");
    if (fpForm) fpForm.style.display = "none";
    const rpForm = document.getElementById("reset-password-form");
    if (rpForm) rpForm.style.display = "none";
    document.getElementById("form-title").innerText = "Login";
    currentMode = "login";
}

let otpInterval = null;

function startOTPTimer() {
    clearInterval(otpInterval);
    const display = document.getElementById("otp-countdown");
    if (!display) return;
    
    // 5 minutes (300 seconds)
    let remaining = 300;
    
    display.innerText = "05:00";
    display.style.color = "#facc15";
    
    otpInterval = setInterval(() => {
        remaining--;
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;
        display.innerText = `${mins}:${secs.toString().padStart(2, '0')}`;
        
        if (remaining <= 0) {
            clearInterval(otpInterval);
            display.innerText = "Expired";
            display.style.color = "#ef4444";
        }
    }, 1000);
}

function showOTP() {
    document.getElementById("login-form").style.display = "none";
    document.getElementById("register-form").style.display = "none";
    document.getElementById("otp-form").style.display = "block";
    document.getElementById("form-title").innerText = "Verify OTP";
    setTimeout(() => document.getElementById("otp")?.focus(), 100);
    startOTPTimer();
}

function setRole(roleType) {
    document.getElementById("role").value = roleType;
    document.getElementById("btn-pro").classList.toggle("active", roleType === "professional");
    document.getElementById("btn-home").classList.toggle("active", roleType === "home");

    const spField = document.getElementById("sp-id-field");
    spField.style.display = roleType === "professional" ? "block" : "none";
    if (roleType !== "professional") {
        document.getElementById("caregiver_id").value = "";
    }
}

function toggleLoginRole() {
    const roleType = document.getElementById("login-role").value;
    const spField = document.getElementById("login-sp-id-field");
    if (spField) {
        spField.style.display = roleType === "professional" ? "block" : "none";
    }
}

function setLoading(btnId, loading, defaultText) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.disabled  = loading;
    
    let loadingText = "Processing…";
    if (btnId === "verify-btn") loadingText = "Verifying…";
    else if (btnId === "login-btn" || btnId === "register-btn" || btnId === "forgot-btn") loadingText = "Sending OTP…";

    btn.innerHTML = loading
        ? `<span class="spinner"></span> ${loadingText}`
        : defaultText;
}

async function register() {
    const data = {
        name:         document.getElementById("name").value.trim(),
        age:          document.getElementById("age").value,
        gender:       document.getElementById("gender").value,
        email:        document.getElementById("email").value.trim(),
        password:     document.getElementById("password").value,
        role:         document.getElementById("role").value,
        caregiver_id: document.getElementById("caregiver_id").value.trim()
    };

    if (!data.name || !data.email || !data.password) {
        alert("Please fill in all required fields.");
        return;
    }

    if (!isValidEmail(data.email)) {
        alert("Please enter a valid email address.");
        return;
    }

    if (data.role === "professional" && !data.caregiver_id) {
        alert("Professionals must create a Caregiver ID.");
        return;
    }

    setLoading("register-btn", true, "Register");
    try {
        const res    = await fetch('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        if (result.status === "otp_sent") {
            showOTP();
        } else {
            alert(result.message || "Registration failed. Please try again.");
        }
    } catch (e) {
        alert("Network error. Please try again.");
    } finally {
        setLoading("register-btn", false, "Register");
    }
}

async function login() {
    const data = {
        email:        document.getElementById("login-email").value.trim(),
        password:     document.getElementById("login-password").value,
        role:         document.getElementById("login-role").value,
        caregiver_id: document.getElementById("login-caregiver-id") ? document.getElementById("login-caregiver-id").value.trim() : ""
    };

    if (!data.email || !data.password) {
        alert("Please enter your email and password.");
        return;
    }

    if (!isValidEmail(data.email)) {
        alert("Please enter a valid email address.");
        return;
    }

    if (data.role === "professional" && !data.caregiver_id) {
        alert("Please enter your Caregiver ID.");
        return;
    }

    setLoading("login-btn", true, "Login");
    try {
        const res    = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        if (result.status === "otp_sent") {
            showOTP();
        } else {
            alert(result.message || "Invalid credentials. Check your details and selected role.");
        }
    } catch (e) {
        alert("Network error. Please try again.");
    } finally {
        setLoading("login-btn", false, "Login");
    }
}

async function verifyOTP() {
    const otpVal = document.getElementById("otp").value.trim();
    if (!otpVal) { alert("Please enter the OTP."); return; }

    const endpoint = currentMode === "register" ? '/auth/verify-register' : '/auth/verify-login';
    const email    = currentMode === "login"
        ? document.getElementById("login-email").value.trim()
        : document.getElementById("email").value.trim();

    setLoading("verify-btn", true, "Verify");
    try {
        const res    = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, otp: otpVal })
        });
        const result = await res.json();

        if (result.status === "registered" || result.status === "success") {
            const pendingRedirect = sessionStorage.getItem('post_login_redirect');

            if (pendingRedirect) {
                sessionStorage.removeItem('post_login_redirect');

                if (pendingRedirect === 'open_modal') {
                    
                    sessionStorage.setItem('show_modal_on_home', '1');
                    window.location.href = '/';
                } else {
                    window.location.href = pendingRedirect;
                }
            } else {
                window.location.href = "/";
            }
        } else {
            alert(result.message || "Invalid OTP. Please try again.");
        }
    } catch (err) {
        alert("Authentication error. Please try again.");
    } finally {
        setLoading("verify-btn", false, "Verify");
    }
}

function showForgotPassword() {
    document.getElementById("login-form").style.display = "none";
    document.getElementById("register-form").style.display = "none";
    document.getElementById("otp-form").style.display = "none";
    document.getElementById("reset-password-form").style.display = "none";
    document.getElementById("forgot-password-form").style.display = "block";
    document.getElementById("form-title").innerText = "Forgot Password";
    // Reset the steps back to step 1 in case user visits again
    document.getElementById("reset-step-otp").style.display = "block";
    document.getElementById("reset-step-password").style.display = "none";
    currentMode = "forgot";
}

async function sendForgotPasswordOTP() {
    const email = document.getElementById("forgot-email").value.trim();
    if (!email) {
        alert("Please enter your email.");
        return;
    }
    if (!isValidEmail(email)) {
        alert("Please enter a valid email address.");
        return;
    }
    setLoading("forgot-btn", true, "Send Code");
    try {
        const res = await fetch('/auth/forgot-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const result = await res.json();
        if (result.status === "otp_sent") {
            document.getElementById("forgot-password-form").style.display = "none";
            // Show reset form at step 1
            document.getElementById("reset-step-otp").style.display = "block";
            document.getElementById("reset-step-password").style.display = "none";
            document.getElementById("reset-otp").value = "";
            document.getElementById("reset-password-form").style.display = "block";
            document.getElementById("form-title").innerText = "Verify Code";
            currentMode = "reset";
        } else {
            alert(result.message || "Failed to send OTP.");
        }
    } catch (e) {
        alert("Network error.");
    } finally {
        setLoading("forgot-btn", false, "Send Code");
    }
}

async function verifyResetOTP() {
    const otpVal = document.getElementById("reset-otp").value.trim();
    const email  = document.getElementById("forgot-email").value.trim();

    if (!otpVal) {
        alert("Please enter the 6-digit code.");
        return;
    }

    setLoading("verify-reset-btn", true, "Verify Code");
    try {
        const res = await fetch('/auth/verify-reset-otp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, otp: otpVal })
        });
        const result = await res.json();
        if (result.status === "verified") {
            // Hide OTP step, reveal password step
            document.getElementById("reset-step-otp").style.display = "none";
            document.getElementById("reset-step-password").style.display = "block";
            document.getElementById("form-title").innerText = "New Password";
            document.getElementById("new-password").focus();
        } else {
            alert(result.message || "Invalid or expired code. Please try again.");
        }
    } catch (e) {
        alert("Network error.");
    } finally {
        setLoading("verify-reset-btn", false, "Verify Code");
    }
}

async function resetPassword() {
    const newPassword = document.getElementById("new-password").value;
    const email       = document.getElementById("forgot-email").value.trim();

    if (!newPassword) {
        alert("Please enter your new password.");
        return;
    }

    setLoading("reset-btn", true, "Change Password");
    try {
        const res = await fetch('/auth/reset-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, new_password: newPassword })
        });
        const result = await res.json();
        if (result.status === "success") {
            alert("Password changed successfully! You can now log in.");
            showLogin();
        } else {
            alert(result.message || "Failed to change password.");
        }
    } catch (e) {
        alert("Network error.");
    } finally {
        setLoading("reset-btn", false, "Change Password");
    }
}
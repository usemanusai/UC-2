# engine/kernel/heuristics.py
# Exhaustive heuristic collections, multi-language detection strings, and CSS selectors.

# Exhaustive CSS selector lists covering virtually ALL login page patterns
# across all major websites, CMS platforms, and authentication providers.
_HEURISTIC_SELECTORS = {
    "email": [
        # ── By ID ──
        "input#login", "input#Login", "input#LOG", "input#log",
        "input#email", "input#Email", "input#EMAIL",
        "input#username", "input#Username", "input#USERNAME",
        "input#user", "input#User", "input#USER",
        "input#userid", "input#userId", "input#UserID",
        "input#user_login", "input#userLogin",
        "input#loginId", "input#login_id", "input#loginID",
        "input#account", "input#Account",
        "input#identifier", "input#Identifier",
        "input#signin-email", "input#signinEmail",
        "input#ap_email", "input#ap_email_login",
        "input#session_key", "input#session_key-login",
        "input#txtUserName", "input#txtUsername", "input#txtLogin",
        "input#txtEmail", "input#txt_username", "input#txt_email",
        "input#field-email", "input#field-username",
        "input#input-email", "input#input-username", "input#input-login",
        "input#loginfmt",  # Microsoft
        "input#i0116",  # Microsoft Live
        "input#identifierId",  # Google
        "input#login_field",  # GitHub
        "input#user_email",  # Rails Devise
        "input#user_login",  # WordPress
        "input#passp-field-login",  # Yandex
        "input#UserName",  # ASP.NET
        "input#j_username",  # Java/Spring
        "input#member_id",  # Korean sites
        "input#mb_id",  # Korean forums
        "input#loginname", "input#login_name",
        "input#credential_0",  # Salesforce
        "input#okta-signin-username",  # Okta
        "input#auth-username",  # Auth0
        "input#phone_email",  # Phone/email combo fields
        "input#phone", "input#Phone", "input#tel",
        "input#mobile", "input#Mobile",
        # ── By name attribute ──
        "input[name='login']", "input[name='Login']",
        "input[name='email']", "input[name='Email']", "input[name='e-mail']",
        "input[name='username']", "input[name='Username']",
        "input[name='user']", "input[name='User']",
        "input[name='user_login']", "input[name='userLogin']",
        "input[name='userid']", "input[name='userId']", "input[name='user_id']",
        "input[name='account']", "input[name='Account']",
        "input[name='identifier']", "input[name='Identifier']",
        "input[name='session_key']",  # LinkedIn
        "input[name='loginfmt']",  # Microsoft
        "input[name='login_field']",  # GitHub
        "input[name='os_username']",  # Atlassian/Jira
        "input[name='j_username']",  # Java EE
        "input[name='credential_0']",  # Salesforce
        "input[name='_username']",  # Symfony
        "input[name='LoginForm[username]']",  # Yii
        "input[name='user[email]']",  # Rails
        "input[name='user[login]']",  # Rails
        "input[name='member_id']",  # Korean
        "input[name='mb_id']",  # Korean
        "input[name='usr']",  # Short forms
        "input[name='uid']",
        "input[name='uname']",
        "input[name='phone']", "input[name='tel']", "input[name='mobile']",
        "input[name='phone_email']",
        "input[name='wp-submit-email']",  # WordPress
        "input[name='log']",  # WordPress
        "input[name='nick']", "input[name='nickname']",
        # ── By type attribute ──
        "input[type='email']",
        "input[type='tel']",
        # ── By autocomplete attribute ──
        "input[autocomplete='username']",
        "input[autocomplete='email']",
        "input[autocomplete='tel']",
        # ── By placeholder text (multi-language) ──
        "input[placeholder*='mail']",
        "input[placeholder*='Mail']",
        "input[placeholder*='email']",
        "input[placeholder*='Email']",
        "input[placeholder*='E-mail']",
        "input[placeholder*='login']",
        "input[placeholder*='Login']",
        "input[placeholder*='Логин']",  # Russian
        "input[placeholder*='логин']",
        "input[placeholder*='Логін']",  # Ukrainian
        "input[placeholder*='логін']",
        "input[placeholder*='username']",
        "input[placeholder*='Username']",
        "input[placeholder*='user name']",
        "input[placeholder*='User Name']",
        "input[placeholder*='phone']",
        "input[placeholder*='Phone']",
        "input[placeholder*='Телефон']",  # Russian
        "input[placeholder*='телефон']",
        "input[placeholder*='Telefon']",  # Turkish/German
        "input[placeholder*='Teléfono']",  # Spanish
        "input[placeholder*='Téléphone']",  # French
        "input[placeholder*='电话']",  # Chinese
        "input[placeholder*='手机']",  # Chinese mobile
        "input[placeholder*='メール']",  # Japanese
        "input[placeholder*='이메일']",  # Korean
        "input[placeholder*='아이디']",  # Korean ID
        "input[placeholder*='Nutzername']",  # German
        "input[placeholder*='Benutzername']",  # German
        "input[placeholder*='Nom d']",  # French
        "input[placeholder*='utilisateur']",  # French
        "input[placeholder*='usuario']",  # Spanish
        "input[placeholder*='Usuário']",  # Portuguese
        "input[placeholder*='Kullanıcı']",  # Turkish
        "input[placeholder*='البريد']",  # Arabic email
        "input[placeholder*='المستخدم']",  # Arabic user
        "input[placeholder*='ชื่อผู้ใช้']",  # Thai
        "input[placeholder*='Tên đăng nhập']",  # Vietnamese
        "input[placeholder*='사용자']",  # Korean user
        "input[placeholder*='用户名']",  # Chinese username
        "input[placeholder*='帳號']",  # Chinese traditional account
        "input[placeholder*='账号']",  # Chinese simplified account
        "input[placeholder*='ユーザー']",  # Japanese user
        "input[placeholder*='Nama pengguna']",  # Malay/Indonesian
        "input[placeholder*='Имя пользователя']",  # Russian full
        "input[placeholder*='Адрес электронной почты']",  # Russian email
        "input[placeholder*='Почта']",  # Russian mail
        "input[placeholder*='이름']",  # Korean name
        "input[placeholder*='ID']",
        "input[placeholder*='id']",
        # ── By aria-label ──
        "input[aria-label*='email']", "input[aria-label*='Email']",
        "input[aria-label*='mail']", "input[aria-label*='Mail']",
        "input[aria-label*='login']", "input[aria-label*='Login']",
        "input[aria-label*='username']", "input[aria-label*='Username']",
        "input[aria-label*='user']", "input[aria-label*='User']",
        "input[aria-label*='Логин']", "input[aria-label*='логин']",
        "input[aria-label*='phone']", "input[aria-label*='Phone']",
        "input[aria-label*='identifier']",
        # ── By data attributes ──
        "input[data-testid='login-email']",
        "input[data-testid='login-username']",
        "input[data-testid='username']",
        "input[data-testid='email']",
        "input[data-testid='email-input']",
        "input[data-testid='login-input']",
        "input[data-qa='login']",
        "input[data-qa='email']",
        "input[data-qa='username']",
        "input[data-name='email']",
        "input[data-name='username']",
        "input[data-field='email']",
        "input[data-field='username']",
        # ── By CSS class patterns ──
        "input.login-input", "input.login-field", "input.login-email",
        "input.email-input", "input.email-field",
        "input.username-input", "input.username-field",
        "input.signin-email", "input.signin-input",
        "input.auth-input", "input.auth-email",
        "input.form-control[type='text']",
        # ── Generic last resorts ──
        "form input[type='text']:first-of-type",
        "form input[type='email']:first-of-type",
        ".login-form input[type='text']",
        ".login-form input[type='email']",
        ".signin-form input[type='text']",
        ".signin-form input[type='email']",
        "#login-form input[type='text']",
        "#login-form input[type='email']",
        "#loginForm input[type='text']",
        "#loginForm input[type='email']",
        "#signin-form input[type='text']",
        "#signin-form input[type='email']",
    ],
    "password": [
        # ── By ID ──
        "input#password", "input#Password", "input#PASSWORD",
        "input#pass", "input#Pass", "input#PASS",
        "input#passwd", "input#Passwd",
        "input#pwd", "input#Pwd", "input#PWD",
        "input#login-password", "input#loginPassword",
        "input#signin-password", "input#signinPassword",
        "input#user_password", "input#userPassword",
        "input#txtPassword", "input#txt_password",
        "input#ap_password",  # Amazon
        "input#session_password",  # LinkedIn
        "input#passp-field-passwd",  # Yandex
        "input#field-password",
        "input#input-password",
        "input#passwordInput",
        "input#j_password",  # Java/Spring
        "input#current-password",
        "input#okta-signin-password",  # Okta
        "input#auth-password",  # Auth0
        "input#credential_1",  # Salesforce
        "input#UserPassword",  # ASP.NET
        "input#member_pw",  # Korean
        "input#mb_password",  # Korean
        # ── By name attribute ──
        "input[name='password']", "input[name='Password']",
        "input[name='pass']", "input[name='Pass']",
        "input[name='passwd']", "input[name='Passwd']",
        "input[name='pwd']", "input[name='Pwd']",
        "input[name='user_password']",
        "input[name='login_password']",
        "input[name='session_password']",  # LinkedIn
        "input[name='j_password']",  # Java EE
        "input[name='credential_1']",  # Salesforce
        "input[name='_password']",  # Symfony
        "input[name='LoginForm[password]']",  # Yii
        "input[name='user[password]']",  # Rails
        "input[name='member_pw']",  # Korean
        "input[name='mb_password']",  # Korean
        "input[name='os_password']",  # Atlassian/Jira
        "input[name='passwort']",  # German
        "input[name='Пароль']",  # Russian
        "input[name='senha']",  # Portuguese
        "input[name='contraseña']",  # Spanish
        "input[name='mot_de_passe']",  # French
        "input[name='sifre']",  # Turkish
        # ── By type attribute (most reliable) ──
        "input[type='password']",
        # ── By autocomplete attribute ──
        "input[autocomplete='current-password']",
        "input[autocomplete='password']",
        # ── By placeholder text (multi-language) ──
        "input[placeholder*='password']",
        "input[placeholder*='Password']",
        "input[placeholder*='Пароль']",  # Russian
        "input[placeholder*='пароль']",
        "input[placeholder*='Парол']",  # Ukrainian/Uzbek
        "input[placeholder*='парол']",
        "input[placeholder*='Passwort']",  # German
        "input[placeholder*='Mot de passe']",  # French
        "input[placeholder*='mot de passe']",
        "input[placeholder*='Contraseña']",  # Spanish
        "input[placeholder*='contraseña']",
        "input[placeholder*='Senha']",  # Portuguese
        "input[placeholder*='senha']",
        "input[placeholder*='Şifre']",  # Turkish
        "input[placeholder*='şifre']",
        "input[placeholder*='كلمة المرور']",  # Arabic
        "input[placeholder*='كلمة السر']",  # Arabic
        "input[placeholder*='密码']",  # Chinese simplified
        "input[placeholder*='密碼']",  # Chinese traditional
        "input[placeholder*='パスワード']",  # Japanese
        "input[placeholder*='비밀번호']",  # Korean
        "input[placeholder*='รหัสผ่าน']",  # Thai
        "input[placeholder*='Mật khẩu']",  # Vietnamese
        "input[placeholder*='Kata sandi']",  # Indonesian
        "input[placeholder*='Hasło']",  # Polish
        "input[placeholder*='Heslo']",  # Czech
        "input[placeholder*='Wachtwoord']",  # Dutch
        "input[placeholder*='Lösenord']",  # Swedish
        "input[placeholder*='Salasana']",  # Finnish
        "input[placeholder*='Adgangskode']",  # Danish
        "input[placeholder*='Passord']",  # Norwegian
        "input[placeholder*='Jelszó']",  # Hungarian
        "input[placeholder*='Parolă']",  # Romanian
        "input[placeholder*='Κωδικός']",  # Greek
        "input[placeholder*='Лозинка']",  # Serbian
        "input[placeholder*='Гасло']",  # Belarusian
        "input[placeholder*='סיסמה']",  # Hebrew
        "input[placeholder*='रहस्यशब्द']",  # Hindi
        "input[placeholder*='পাসওয়ার্ড']",  # Bengali
        # ── By aria-label ──
        "input[aria-label*='password']", "input[aria-label*='Password']",
        "input[aria-label*='Пароль']", "input[aria-label*='пароль']",
        "input[aria-label*='Passwort']",
        "input[aria-label*='mot de passe']",
        "input[aria-label*='contraseña']",
        # ── By data attributes ──
        "input[data-testid='login-password']",
        "input[data-testid='password']",
        "input[data-testid='password-input']",
        "input[data-qa='password']",
        "input[data-name='password']",
        "input[data-field='password']",
        # ── By CSS class patterns ──
        "input.password-input", "input.password-field",
        "input.login-password", "input.signin-password",
        "input.auth-password",
        # ── Generic last resorts ──
        "form input[type='password']:first-of-type",
        ".login-form input[type='password']",
        ".signin-form input[type='password']",
        "#login-form input[type='password']",
        "#loginForm input[type='password']",
        "#signin-form input[type='password']",
    ],
    "submit": [
        # ── By type ──
        "button[type='submit']",
        "input[type='submit']",
        # ── By ID ──
        "button#login-btn", "button#loginBtn", "button#login_btn",
        "button#signin-btn", "button#signinBtn", "button#signin_btn",
        "button#submit-btn", "button#submitBtn", "button#submit_btn",
        "button#sign-in", "button#signIn", "button#sign_in",
        "button#log-in", "button#logIn", "button#log_in",
        "button#btnLogin", "button#btn_login", "button#btn-login",
        "button#btnSignIn", "button#btn_signin", "button#btn-signin",
        "button#btnSubmit", "button#btn_submit", "button#btn-submit",
        "button#idSIButton9",  # Microsoft
        "button#passwordNext",  # Google
        "input#submit", "input#Submit",
        "input#login-submit",
        "button#kc-login",  # Keycloak
        "button#okta-signin-submit",  # Okta
        # ── By name ──
        "button[name='login']", "button[name='Login']",
        "button[name='signin']", "button[name='SignIn']",
        "button[name='submit']", "button[name='Submit']",
        "input[name='login']", "input[name='Login']",
        "input[name='signin']", "input[name='SignIn']",
        "input[name='submit']", "input[name='Submit']",
        "input[name='wp-submit']",  # WordPress
        "input[name='commit']",  # Rails
        # ── By CSS class ──
        "button.btn-primary",
        "button.btn-login", "button.btn-signin",
        "button.login-button", "button.signin-button", "button.sign-in-button",
        "button.login-btn", "button.signin-btn",
        "button.submit-button", "button.submit-btn",
        "button.auth-button", "button.auth-btn",
        "button.btn-lg.btn-primary",
        "button.btn-block.btn-primary",
        "input.btn-primary",
        "input.btn-login",
        "input.login-button",
        "input.submit-button",
        # ── By data attributes ──
        "[data-testid='login-submit']",
        "[data-testid='signin-submit']",
        "[data-testid='submit-button']",
        "[data-testid='login-button']",
        "[data-testid='signin-button']",
        "[data-qa='login-submit']",
        "[data-qa='signin-submit']",
        "[data-action='login']",
        "[data-action='signin']",
        "[data-action='sign-in']",
        # ── By value attribute (input[type=submit]) ──
        "input[value='Sign in']", "input[value='Sign In']", "input[value='Signin']",
        "input[value='Login']", "input[value='Log in']", "input[value='Log In']",
        "input[value='Submit']",
        "input[value='Войти']",  # Russian
        "input[value='ВОЙТИ']",
        "input[value='Вхід']",  # Ukrainian
        "input[value='Giriş']",  # Turkish
        "input[value='Anmelden']",  # German
        "input[value='Connexion']",  # French
        "input[value='Iniciar sesión']",  # Spanish
        "input[value='Entrar']",  # Portuguese
        "input[value='ログイン']",  # Japanese
        "input[value='登录']",  # Chinese simplified
        "input[value='登入']",  # Chinese traditional
        "input[value='로그인']",  # Korean
        "input[value='تسجيل الدخول']",  # Arabic
        "input[value='เข้าสู่ระบบ']",  # Thai
        "input[value='Đăng nhập']",  # Vietnamese
        "input[value='Masuk']",  # Indonesian
        # ── By aria-label ──
        "button[aria-label*='Sign in']", "button[aria-label*='sign in']",
        "button[aria-label*='Log in']", "button[aria-label*='log in']",
        "button[aria-label*='Login']", "button[aria-label*='login']",
        "button[aria-label*='Submit']", "button[aria-label*='submit']",
        "button[aria-label*='Войти']", "button[aria-label*='войти']",
        "button[aria-label*='Вход']", "button[aria-label*='вход']",
        # ── Generic form submit (last resorts) ──
        "form button:not([type='button'])",
        "form button:last-of-type",
        ".login-form button",
        ".signin-form button",
        "#login-form button",
        "#loginForm button",
        "#signin-form button",
        "#signinForm button",
        ".form-horizontal button[type='submit']",
    ],
    "next": [
        # ── By ID ──
        "button#next", "button#Next", "button#nextBtn", "button#next-btn",
        "button#btn-next", "button#btnNext",
        "button#identifierNext",  # Google
        "button#continue", "button#Continue",
        "button#proceed", "button#Proceed",
        "input#next", "input#Next",
        # ── By name ──
        "button[name='next']", "button[name='Next']",
        "button[name='continue']", "button[name='Continue']",
        "input[name='next']", "input[name='Next']",
        # ── By CSS class ──
        "button.btn-next", "button.next-button", "button.next-btn",
        "button.continue-button", "button.continue-btn",
        "button.btn-continue",
        "button.step-next",
        # ── By value attribute ──
        "input[value='Next']", "input[value='next']",
        "input[value='Continue']", "input[value='continue']",
        "input[value='Далее']",  # Russian
        "input[value='Продолжить']",  # Russian
        "input[value='Weiter']",  # German
        "input[value='Suivant']",  # French
        "input[value='Siguiente']",  # Spanish
        "input[value='Próximo']",  # Portuguese
        "input[value='İleri']",  # Turkish
        "input[value='التالي']",  # Arabic
        "input[value='次へ']",  # Japanese
        "input[value='下一步']",  # Chinese
        "input[value='다음']",  # Korean
        "input[value='ถัดไป']",  # Thai
        "input[value='Tiếp']",  # Vietnamese
        # ── By aria-label ──
        "button[aria-label*='Next']", "button[aria-label*='next']",
        "button[aria-label*='Continue']", "button[aria-label*='continue']",
        "button[aria-label*='Далее']", "button[aria-label*='далее']",
        "button[aria-label*='Продолжить']",
        # ── By data attributes ──
        "[data-testid='next-button']",
        "[data-testid='continue-button']",
        "[data-testid='next']",
        "[data-action='next']",
        "[data-action='continue']",
    ],
}

# Exhaustive multi-language error text patterns for detecting login failures.
_ERROR_TEXT_PATTERNS = {
    "invalid_credentials": [
        # ── English ──
        "incorrect password", "incorrect username", "incorrect login",
        "incorrect email", "incorrect credentials",
        "invalid password", "invalid username", "invalid login",
        "invalid email", "invalid credentials",
        "wrong password", "wrong username", "wrong login", "wrong email",
        "wrong credentials",
        "bad password", "bad credentials",
        "login failed", "sign in failed", "signin failed",
        "authentication failed", "auth failed",
        "account not found", "user not found", "email not found",
        "no account found", "doesn't exist", "does not exist",
        "not recognized", "unrecognized",
        "password is incorrect", "username is incorrect",
        "password doesn't match", "password does not match",
        "the email and password", "email or password",
        "username or password", "login or password",
        "could not sign you in", "unable to sign in",
        "unable to log in", "could not log you in",
        "access denied", "login denied", "forbidden",
        "please try again", "try again",
        "check your credentials", "verify your credentials",
        "we didn't recognize", "we did not recognize",
        "this account has been", "your account has been",
        "too many attempts", "too many failed",
        "account locked", "account disabled", "account suspended",
        "account blocked", "temporarily locked",
        # ── Russian ──
        "неверный пароль", "неверный логин", "неверные данные",
        "неверное имя пользователя", "неверный email",
        "неправильный пароль", "неправильный логин",
        "неправильное имя пользователя",
        "ошибка входа", "ошибка авторизации", "ошибка аутентификации",
        "неверная пара логин", "неверная комбинация",
        "логин или пароль", "пароль или логин",
        "пользователь не найден", "аккаунт не найден",
        "учётная запись не найдена", "учетная запись не найдена",
        "доступ запрещён", "доступ запрещен", "доступ закрыт",
        "попробуйте ещё раз", "попробуйте еще раз",
        "вы ввели неверный", "вы ввели неправильный",
        "проверьте правильность", "проверьте данные",
        "аккаунт заблокирован", "учётная запись заблокирована",
        "слишком много попыток",
        # ── Ukrainian ──
        "невірний пароль", "невірний логін", "невірні дані",
        "помилка входу", "помилка авторизації",
        "неправильний пароль", "неправильний логін",
        "спробуйте ще раз",
        # ── German ──
        "falsches passwort", "falscher benutzername",
        "ungültiges passwort", "ungültiger benutzername",
        "falsche anmeldedaten", "ungültige anmeldedaten",
        "anmeldung fehlgeschlagen", "login fehlgeschlagen",
        "zugang verweigert", "konto gesperrt",
        "benutzername oder passwort",
        "versuchen sie es erneut",
        # ── French ──
        "mot de passe incorrect", "identifiant incorrect",
        "mot de passe invalide", "identifiant invalide",
        "identifiants incorrects", "identifiants invalides",
        "connexion échouée", "échec de connexion",
        "accès refusé", "compte bloqué",
        "nom d'utilisateur ou mot de passe",
        "veuillez réessayer",
        # ── Spanish ──
        "contraseña incorrecta", "usuario incorrecto",
        "contraseña inválida", "usuario inválido",
        "credenciales incorrectas", "credenciales inválidas",
        "inicio de sesión fallido", "error de inicio de sesión",
        "acceso denegado", "cuenta bloqueada",
        "nombre de usuario o contraseña",
        "intente de nuevo", "inténtelo de nuevo",
        # ── Portuguese ──
        "senha incorreta", "usuário incorreto",
        "senha inválida", "usuário inválido",
        "credenciais incorretas", "credenciais inválidas",
        "login falhou", "erro de login",
        "acesso negado", "conta bloqueada",
        "nome de usuário ou senha",
        "tente novamente",
        # ── Italian ──
        "password errata", "username errato",
        "password non valida", "credenziali errate",
        "accesso negato", "account bloccato",
        "nome utente o password",
        "riprova",
        # ── Turkish ──
        "yanlış şifre", "yanlış kullanıcı adı",
        "geçersiz şifre", "geçersiz kullanıcı adı",
        "giriş başarısız", "hatalı giriş",
        "erişim reddedildi", "hesap kilitli",
        "kullanıcı adı veya şifre",
        "tekrar deneyin",
        # ── Arabic ──
        "كلمة المرور غير صحيحة", "اسم المستخدم غير صحيح",
        "بيانات الاعتماد غير صحيحة",
        "فشل تسجيل الدخول", "خطأ في تسجيل الدخول",
        "تم رفض الوصول", "الحساب مقفل",
        "اسم المستخدم أو كلمة المرور",
        "حاول مرة أخرى",
        # ── Chinese (Simplified) ──
        "密码错误", "用户名错误", "密码不正确", "用户名不正确",
        "登录失败", "认证失败", "验证失败",
        "账户被锁定", "账户已锁定", "账号已锁定",
        "用户名或密码", "请重试", "再试一次",
        "凭证无效", "凭据错误",
        # ── Chinese (Traditional) ──
        "密碼錯誤", "使用者名稱錯誤", "登入失敗",
        "帳戶已鎖定", "使用者名稱或密碼", "請重試",
        # ── Japanese ──
        "パスワードが正しくありません", "ユーザー名が正しくありません",
        "パスワードが違います", "ログインに失敗",
        "認証に失敗", "アカウントがロック",
        "ユーザー名またはパスワード", "もう一度お試し",
        "メールアドレスまたはパスワード",
        # ── Korean ──
        "비밀번호가 올바르지 않습니다", "사용자 이름이 올바르지 않습니다",
        "비밀번호가 틀렸습니다", "로그인 실패",
        "인증 실패", "계정이 잠겼습니다",
        "아이디 또는 비밀번호", "다시 시도",
        # ── Polish ──
        "błędne hasło", "nieprawidłowe hasło",
        "nieprawidłowa nazwa użytkownika",
        "logowanie nie powiodło się",
        "nazwa użytkownika lub hasło",
        "spróbuj ponownie",
        # ── Czech ──
        "nesprávné heslo", "neplatné heslo",
        "přihlášení se nezdařilo",
        "uživatelské jméno nebo heslo",
        "zkuste to znovu",
        # ── Dutch ──
        "onjuist wachtwoord", "ongeldig wachtwoord",
        "inloggen mislukt", "aanmelden mislukt",
        "gebruikersnaam of wachtwoord",
        "probeer het opnieuw",
        # ── Thai ──
        "รหัสผ่านไม่ถูกต้อง", "ชื่อผู้ใช้ไม่ถูกต้อง",
        "เข้าสู่ระบบล้มเหลว",
        "ชื่อผู้ใช้หรือรหัสผ่าน", "ลองอีกครั้ง",
        # ── Vietnamese ──
        "mật khẩu không đúng", "tên đăng nhập không đúng",
        "đăng nhập thất bại",
        "tên đăng nhập hoặc mật khẩu", "thử lại",
        # ── Indonesian / Malay ──
        "kata sandi salah", "nama pengguna salah",
        "login gagal", "masuk gagal",
        "nama pengguna atau kata sandi", "coba lagi",
        # ── Hindi ──
        "गलत पासवर्ड", "गलत उपयोगकर्ता नाम",
        "लॉगिन विफल", "पुन: प्रयास करें",
        # ── Greek ──
        "λάθος κωδικός", "λανθασμένος κωδικός",
        "αποτυχία σύνδεσης", "δοκιμάστε ξανά",
        # ── Swedish, Norwegian, Danish, Finnish ──
        "felaktigt lösenord", "ugyldig passord", "forkert adgangskode",
        "väärä salasana",
        "inloggningen misslyckades",
        "innlogging mislyktes",
        "login mislykkedes",
        "kirjautuminen epäonnistui",
        # ── Hebrew ──
        "סיסמה שגויה", "שם משתמש שגוי",
        "כניסה נכשלה", "נסה שוב",
        # ── Romanian ──
        "parolă incorectă", "autentificare eșuată",
        "numele de utilizator sau parola", "încercați din nou",
        # ── Hungarian ──
        "helytelen jelszó", "bejelentkezés sikertelen",
        "felhasználónév vagy jelszó", "próbálja újra",
    ],
    "captcha_error": [
        # ── English ──
        "captcha", "CAPTCHA", "Captcha",
        "verification failed", "verification error",
        "captcha incorrect", "captcha wrong", "captcha invalid",
        "captcha expired", "captcha failed",
        "security check failed", "security verification failed",
        "please complete the captcha", "please solve the captcha",
        "robot verification", "bot verification",
        "human verification", "are you a robot",
        "prove you are human", "prove you're human",
        "recaptcha", "reCAPTCHA", "ReCAPTCHA",
        "hcaptcha", "hCaptcha",
        "turnstile",
        "challenge failed", "challenge expired",
        # ── Russian ──
        "капча", "КАПЧА", "Капча",
        "каптча",
        "проверка не пройдена", "проверка не удалась",
        "неверная капча", "неправильная капча",
        "код с картинки", "введите код",
        "защитный код", "код подтверждения",
        "антибот", "проверка безопасности",
        # ── Ukrainian ──
        "перевірка не пройдена", "невірна капча",
        # ── German ──
        "captcha falsch", "sicherheitsprüfung fehlgeschlagen",
        "captcha ungültig", "bestätigungscode",
        # ── French ──
        "captcha incorrect", "vérification échouée",
        "captcha invalide", "code de sécurité",
        # ── Spanish ──
        "captcha incorrecto", "verificación fallida",
        "captcha inválido", "código de seguridad",
        # ── Portuguese ──
        "captcha incorreto", "verificação falhou",
        "captcha inválido", "código de segurança",
        # ── Turkish ──
        "captcha yanlış", "doğrulama başarısız",
        "güvenlik kodu",
        # ── Arabic ──
        "رمز التحقق خاطئ", "فشل التحقق",
        "رمز الأمان",
        # ── Chinese ──
        "验证码错误", "验证码不正确", "验证码过期",
        "验证失败", "安全验证", "图形验证码",
        "驗證碼錯誤", "驗證碼不正確",
        # ── Japanese ──
        "キャプチャ", "認証コードが正しくありません",
        "セキュリティチェック",
        # ── Korean ──
        "보안 문자", "인증 코드가 올바르지 않습니다",
        "보안 확인 실패",
        # ── Polish ──
        "captcha niepoprawna", "weryfikacja nie powiodła się",
        # ── Dutch ──
        "captcha onjuist", "verificatie mislukt",
        # ── Thai ──
        "รหัสยืนยันไม่ถูกต้อง",
        # ── Vietnamese ──
        "mã xác nhận không đúng", "xác minh thất bại",
        # ── Indonesian ──
        "kode verifikasi salah", "verifikasi gagal",
    ],
}

# CSS selectors commonly used for error message containers across login pages
_ERROR_CSS_SELECTORS = {
    "invalid_credentials": [
        "div.alert-danger", "div.alert-error", "div.alert-warning",
        "div.error", "div.error-message", "div.error-msg",
        "div.login-error", "div.signin-error", "div.auth-error",
        "span.error", "span.error-message", "span.error-msg",
        "p.error", "p.error-message", "p.error-msg",
        "div[role='alert']", "div[role='status']",
        "div.notification-error", "div.notification--error",
        "div.message-error", "div.msg-error",
        "div.form-error", "div.form-error-message",
        "div.field-error", "div.field-validation-error",
        "div.validation-error", "div.validation-message",
        "div.flash-error", "div.flash--error",
        "div.toast-error", "div.toast--error",
        "div.banner-error", "div.banner--error",
        "span.field-error", "span.field-validation-error",
        "span.help-block", "span.help-inline",
        "label.error", "label.error-message",
        "div.invalid-feedback", "span.invalid-feedback",
        "div.text-danger", "span.text-danger", "p.text-danger",
        "div.text-error", "span.text-error",
        "div.has-error", "div.is-invalid",
        ".alert.alert-danger", ".alert.alert-error",
        ".notice.notice-error", ".notice--error",
        "[data-testid='error-message']",
        "[data-testid='login-error']",
        "[data-testid='auth-error']",
        "[data-qa='error']",
        "[data-qa='login-error']",
        "div.ant-alert-error",  # Ant Design
        "div.MuiAlert-standardError",  # Material UI
        "div.el-message--error",  # Element UI
        "div.v-alert--error",  # Vuetify
        "div.chakra-alert[data-status='error']",  # Chakra UI
        "#error-message", "#errorMessage", "#error_message",
        "#login-error", "#loginError", "#login_error",
        "#auth-error", "#authError", "#auth_error",
        ".error-container", "#error-container",
    ],
    "captcha_error": [
        "div.captcha-error", "div.captcha-error-message",
        "span.captcha-error", "span.captcha-error-message",
        "div.recaptcha-error", "div.hcaptcha-error",
        "div.g-recaptcha-error",
        "div.captcha-warning", "div.captcha-alert",
        "div.challenge-error",
        "#captcha-error", "#captchaError", "#captcha_error",
        "#rc-anchor-alert",  # reCAPTCHA
        ".rc-anchor-error-msg",  # reCAPTCHA
        ".rc-anchor-error-message",  # reCAPTCHA
        "div[data-testid='captcha-error']",
        "div[data-qa='captcha-error']",
        # General error containers also used for captcha
        "div.alert-danger", "div.alert-error",
        "div[role='alert']",
    ],
}

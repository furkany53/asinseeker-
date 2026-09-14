import time

from keepa_check import open_cdp_session

# Keepa'nin gercek giris formundan (canli CDP ile) dogrulanan alan ID'leri:
# #username, #password, #submitLogin -- 2FA acik hesaplarda ek olarak
# #otp / #submitLoginOtp gorunuyor (bunu otomatik dolduramayiz, kullanici
# gormesi icin pencereyi acik/gorunur birakiyoruz).
OPEN_LOGIN_MODAL_EXPR = """
(function () {
    var els = Array.from(document.querySelectorAll('a,button,div,span'));
    var el = els.find(function (e) { return (e.innerText || '').trim() === 'Log In'; });
    if (el) { el.click(); return true; }
    return false;
})()
"""

FILL_LOGIN_EXPR = """
(function (username, password) {
    function setValue(el, value) {
        if (!el) return false;
        var proto = Object.getPrototypeOf(el);
        var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }
    var userEl = document.getElementById('username');
    var passEl = document.getElementById('password');
    var ok = setValue(userEl, username) && setValue(passEl, password);
    return ok;
})(%(username)r, %(password)r)
"""

CLICK_SUBMIT_EXPR = """
(function () {
    var btn = document.getElementById('submitLogin');
    if (btn) { btn.click(); return true; }
    return false;
})()
"""

LOGIN_STATE_EXPR = """
(function () {
    var otp = document.getElementById('otp');
    var otpVisible = !!(otp && (otp.offsetWidth || otp.offsetHeight));
    var stillShowsLoginLink = Array.from(document.querySelectorAll('a,button,div,span'))
        .some(function (e) { return (e.innerText || '').trim() === 'Log In'; });
    return { otpVisible: otpVisible, stillShowsLoginLink: stillShowsLoginLink };
})()
"""


def attempt_keepa_login(debug_address, username, password):
    """Keepa'nin giris formunu otomatik doldurup gonderir. Sonuc:
    'submitted_awaiting_otp' | 'submitted' | 'form_not_found'
    Gercek basari (sifre doğru mu, 2FA var mi) tarayici penceresinde
    kullaniciya birakilir -- pencere GORUNUR (headless=False) acilmali."""
    tab, session = open_cdp_session(debug_address)
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.navigate", {"url": "https://keepa.com/"})
        time.sleep(4)

        session.call("Runtime.evaluate", {"expression": OPEN_LOGIN_MODAL_EXPR})
        time.sleep(1.5)

        fill_expr = FILL_LOGIN_EXPR % {
            "username": username,
            "password": password,
        }
        filled = session.eval_json(fill_expr)
        if not filled:
            return "form_not_found"

        session.call("Runtime.evaluate", {"expression": CLICK_SUBMIT_EXPR})
        time.sleep(2.5)

        state = session.eval_json(LOGIN_STATE_EXPR)
        if state and state.get("otpVisible"):
            return "submitted_awaiting_otp"
        return "submitted"
    finally:
        session.close()
        # Sekmeyi kasitli olarak KAPATMIYORUZ: kullanici OTP/CAPTCHA gorup
        # tamamlayabilsin diye pencere acik kalmali. Chrome'u ne zaman
        # kapatacagina cagiran taraf karar verir.

import random, time
OTP_EXPIRY_SECS = 300

def gen() -> str:
    return f"{random.randint(0, 999999):06d}"

def begin_otp(st: dict, target: str):
    st["otp"]["target"] = target
    st["otp"][target]["code"] = gen()
    st["otp"][target]["ts"] = time.time()
    st["otp"][target]["verified"] = False

def check(st: dict, target: str, code: str) -> str | None:
    data = st["otp"][target]
    if not data["code"]:
        return "No OTP in progress. Type 'resend' to get a new OTP."
    if time.time() - data["ts"] > OTP_EXPIRY_SECS:
        return "OTP expired. Type 'resend' to get a new OTP."
    if code != data["code"]:
        return "Incorrect OTP. Please try again or type 'resend'."
    data["verified"] = True
    data["code"] = None
    data["ts"] = 0
    return None

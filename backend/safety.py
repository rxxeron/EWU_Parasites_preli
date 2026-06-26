import re
def enforce_safety(response_dict: dict, input_language: str = "en") -> dict:
    """
    Enforces the mandatory safety rules on response fields.
    Modifies the dictionary in-place.
    """
    reply = response_dict.get("customer_reply", "")
    if not isinstance(reply, str):
        reply = str(reply) if reply is not None else ""
    action = response_dict.get("recommended_next_action", "")
    if not isinstance(action, str):
        action = str(action) if action is not None else ""
    reply_lower = reply.lower()
    action_lower = action.lower()
    credentials_en = ["pin", "otp", "password", "passcode", "card number", "cvv"]
    verbs_en = ["please", "send", "provide", "give", "enter", "share", "verify", "type", "collect"]
    has_violation_en = False
    for c in credentials_en:
        if c in reply_lower:
            c_idx = reply_lower.find(c)
            for v in verbs_en:
                if v in reply_lower:
                    v_idx = reply_lower.find(v)
                    if abs(c_idx - v_idx) < 50:
                        min_idx = min(c_idx, v_idx)
                        max_idx = max(c_idx + len(c), v_idx + len(v))
                        start_idx = max(0, min_idx - 20)
                        end_idx = min(len(reply_lower), max_idx + 20)
                        context = reply_lower[start_idx:end_idx]
                        if "do not" in context or "don't" in context or "never" in context or "no" in context:
                            continue
                        has_violation_en = True
                        break
            if has_violation_en:
                break
    credentials_bn = ["পিন", "ওটিপি", "পাসওয়ার্ড", "পাসওয়ার্ড", "কার্ড", "পিন নম্বর", "পিন নাম্বার"]
    verbs_bn = ["দিন", "পাঠান", "বলুন", "শেয়ার", "শেয়ার", "টাইপ", "লিখুন", "ইনপুট", "সাবমিট"]
    has_violation_bn = False
    for c in credentials_bn:
        if c in reply_lower:
            c_idx = reply_lower.find(c)
            for v in verbs_bn:
                if v in reply_lower:
                    v_idx = reply_lower.find(v)
                    if abs(c_idx - v_idx) < 50:
                        min_idx = min(c_idx, v_idx)
                        max_idx = max(c_idx + len(c), v_idx + len(v))
                        start_idx = max(0, min_idx - 20)
                        end_idx = min(len(reply_lower), max_idx + 20)
                        context = reply_lower[start_idx:end_idx]
                        if any(neg in context for neg in ["করবেন না", "কাউকে বলবেন না", "দেবেন না", "শেয়ার করবেন না", "শেয়ার করবেন না", "বলবেন না", "জানাবেন না"]):
                            continue
                        has_violation_bn = True
                        break
            if has_violation_bn:
                break
    if has_violation_en or has_violation_bn:
        if input_language == "bn":
            response_dict["customer_reply"] = (
                "আপনার অনুরোধটি পর্যালোচনার জন্য আমাদের টিমকে পাঠানো হয়েছে। আমরা নিরাপত্তা নিশ্চিত করতে আপনার টিকিটটি খতিয়ে দেখছি। "
                "অনুগ্রহ করে মনে রাখবেন, আমরা বা আমাদের কোনো প্রতিনিধি কখনই আপনার পিন (PIN), ওটিপি (OTP), বা পাসওয়ার্ড জানতে চাইব না। "
                "নিরাপত্তা স্বার্থে এগুলো কারো সাথে শেয়ার করবেন না। আমরা অফিসিয়াল চ্যানেলের মাধ্যমে আপনার সাথে শীঘ্রই যোগাযোগ করব।"
            )
        else:
            response_dict["customer_reply"] = (
                "Your ticket has been successfully escalated to our specialized support team for further review. "
                "We are investigating the incident to ensure your account security. Please be advised that our representatives "
                "will never ask for your PIN, OTP, or password. Keep these credentials strictly private. "
                "We will contact you shortly through official support channels."
            )
        response_dict["recommended_next_action"] = "Review the customer's account for suspicious activity. Note: Original action was sanitized due to credential request."
        response_dict["human_review_required"] = True
        reply_lower = response_dict["customer_reply"].lower()
    refund_promise_pattern = r"\b(?:we will|i will|system will|we're going to|i'll|will|have|has|already)\b[^.]*?\b(?:refund|reverse|unblock|approve|credit|send back|return your money|give back)\b|\b(?:refund|reversal|unblock)\b[^.]*?\b(?:has been|will be|is initiated|initiated|completed|processed)\b|\b(?:you will receive|receive your|get your)\b[^.]*?\b(?:refund|money back|reversal)\b"
    refund_promise_pattern_bn = r"(?:ফেরত দেব|রিফান্ড করব|টাকা পাঠিয়ে দেব|আনব্লক করব|ফেরত দেওয়া হয়েছে|রিফান্ড করা হয়েছে|সম্পন্ন হয়েছে)"
    if re.search(refund_promise_pattern, reply_lower) or re.search(refund_promise_pattern_bn, reply_lower):
        if input_language == "bn":
            response_dict["customer_reply"] = (
                "আপনার অনুরোধটি সফলভাবে নথিভুক্ত করা হয়েছে এবং এটি পর্যালোচনার জন্য পাঠানো হয়েছে। "
                "যাচাইকরণ সাপেক্ষে যেকোনো যোগ্য অর্থ অফিসিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
                "অনুগ্রহ করে নিরাপত্তার স্বার্থে আপনার পিন (PIN) বা ওটিপি (OTP) কারো সাথে শেয়ার করবেন না।"
            )
        else:
            response_dict["customer_reply"] = (
                "We have received your request and logged it for official review. "
                "Please note that any eligible refund or reversal amount will be credited back to your account through official channels "
                "only after completing the audit. Please do not share your PIN or OTP with anyone."
            )
    if re.search(refund_promise_pattern, action_lower):
        response_dict["recommended_next_action"] = (
            "Verify details and initiate the appropriate dispute review flow. "
            "Do not promise refund status until official review completes."
        )
        action_lower = response_dict["recommended_next_action"].lower()
    url_pattern = r"(https?://[^\s]+)"
    phone_pattern = r"(\+?[\d-]{8,15})"
    if re.search(url_pattern, reply_lower):
        response_dict["customer_reply"] = re.sub(url_pattern, "official support channels", response_dict.get("customer_reply", ""))
    if re.search(phone_pattern, reply_lower):
        response_dict["customer_reply"] = re.sub(r"\+?880\d{8,11}\b|\b01\d{9}\b", "our support center", response_dict.get("customer_reply", ""))
    reply_now = response_dict.get("customer_reply", "")
    reply_now_lower = reply_now.lower()
    if input_language == "bn":
        safety_bn = "পিন বা ওটিপি শেয়ার করবেন না"
        if safety_bn not in reply_now:
            if reply_now.rstrip().endswith("."):
                response_dict["customer_reply"] = reply_now.rstrip() + " অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            else:
                response_dict["customer_reply"] = reply_now.rstrip() + ". অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
    else:
        safety_en = "pin or otp"
        if safety_en not in reply_now_lower:
            if reply_now.rstrip().endswith("."):
                response_dict["customer_reply"] = reply_now.rstrip() + " Please do not share your PIN or OTP with anyone."
            else:
                response_dict["customer_reply"] = reply_now.rstrip() + ". Please do not share your PIN or OTP with anyone."
    response_dict["customer_reply"] = response_dict["customer_reply"].strip()
    response_dict["recommended_next_action"] = response_dict["recommended_next_action"].strip()
    return response_dict
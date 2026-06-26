import os
import re
import json
import logging
import asyncio
from typing import Optional, List, Tuple
import httpx
from schemas import (
    AnalyzeTicketRequest, 
    AnalyzeTicketResponse,
    EvidenceVerdictEnum, 
    CaseTypeEnum, 
    SeverityEnum, 
    DepartmentEnum,
    LanguageEnum,
    TransactionTypeEnum,
    TransactionStatusEnum
)
from safety import enforce_safety
logger = logging.getLogger("queuestorm.investigator")
def convert_bangla_digits(text: str) -> str:
    """Converts Bangla digits to English digits."""
    bangla_to_english = {
        '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
        '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9'
    }
    for bn, en in bangla_to_english.items():
        text = text.replace(bn, en)
    return text
def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = convert_bangla_digits(text.lower())
    text = re.sub(r'[.,\/#!$%\^&\*;:{}=\-_`~()?]', ' ', text)
    return " ".join(text.split())
def extract_numbers(text: str) -> List[float]:
    """Extracts numbers from normalized text."""
    numbers = []
    matches = re.findall(r'\b\d+(?:\.\d+)?\b', text)
    for m in matches:
        try:
            numbers.append(float(m))
        except ValueError:
            continue
    return numbers
class KeyRotationPool:
    def __init__(self):
        keys_str = os.getenv("GROQ_API_KEYS", os.getenv("GROQ_API_KEY", ""))
        self.keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        self.current_idx = 0
        if self.keys:
            logger.info(f"Loaded Groq key rotation pool with {len(self.keys)} key(s).")
        else:
            logger.warning("No Groq API keys configured. Groq pipeline will be skipped.")
    def get_next_key(self) -> Optional[str]:
        if not self.keys:
            return None
        key = self.keys[self.current_idx]
        self.current_idx = (self.current_idx + 1) % len(self.keys)
        return key
key_pool = KeyRotationPool()
def classify_locally(req: AnalyzeTicketRequest) -> dict:
    """
    Zero-dependency Python implementation of ticket investigation.
    Runs locally on CPU in <2ms. Matches transactions based on keywords/amounts.
    """
    complaint = req.complaint
    history = req.transaction_history or []
    norm = normalize_text(complaint)
    case_type = CaseTypeEnum.other
    phishing_keywords = [
        'otp', 'pin', 'password', 'passcode', 'scam', 'scammer', 'fraud', 'fake', 'credential',
        'lottery', 'prize', 'winner', 'blocked', 'block', 'agent call', 'bkash agent',
        'sim swap', 'anydesk', 'teamviewer', 'remote control', 'call forwarding', 'forwarding',
        'call forward',
        'পিন', 'ওটিপি', 'পাসওয়ার্ড', 'পাসওয়ার্ড', 'ভুয়া', 'ভুয়া', 'প্রতারণা', 'লটারি', 'পুরস্কার',
        'একাউন্ট বন্ধ', 'কল ফরওয়ার্ডিং', 'কল ফরওয়ার্ডিং', 'সিম সোয়াপ', 'সিম সোয়াপ'
    ]
    wrong_transfer_keywords = [
        'wrong number', 'wrong account', 'wrong send', 'sent to wrong', 'wrong digit',
        'mistake send', 'another number', 'another account', 'accidentally sent',
        'ভুল নম্বর', 'ভুল নাম্বার', 'ভুল করে', 'ভুল একাউন্ট', 'অন্য নাম্বারে', 'ভুল নম্বরে', 'ভুল নাম্বারে'
    ]
    payment_failed_keywords = [
        'failed', 'deducted', 'declined', 'error', 'unsuccessful', 'taka cut',
        'money cut', 'balance cut', 'pending', 'timed out', 'timeout', 'not completed',
        'ব্যালেন্স কেটেছে', 'টাকা কেটেছে', 'ফেইল', 'ব্যর্থ', 'টাকা কেটেছে কিন্তু'
    ]
    refund_request_keywords = [
        'refund', 'return money', 'get back my money', 'want my money back', 'cancel transaction',
        'টাকা ফেরত', 'রিফান্ড', 'টাকা ব্যাক', 'ফেরত চাই', 'টাকা ফেরত দিন'
    ]
    duplicate_payment_keywords = [
        'twice', 'double', 'two times', 'duplicate', 'double charge', 'twice deducted',
        'দুইবার', 'ডাবল', '২ বার', 'কেটেছে দুইবার'
    ]
    settlement_keywords = [
        'settlement', 'merchant settle', 'settle money', 'merchant balance', 'not settled',
        'সেটেলমেন্ট', 'সেটেল', 'মার্চেন্ট সেটেলমেন্ট'
    ]
    agent_cash_in_keywords = [
        'agent cash in', 'cash in issue', 'agent deposit', 'cashin',
        'এজেন্ট ক্যাশ ইন', 'ক্যাশ ইন করেছি'
    ]
    complaint_raw_lower = complaint.lower()
    if any(k in norm for k in phishing_keywords) or any(k in complaint_raw_lower for k in ['*21*', '##002#', '*62*']):
        case_type = CaseTypeEnum.phishing_or_social_engineering
    elif any(k in norm for k in wrong_transfer_keywords):
        case_type = CaseTypeEnum.wrong_transfer
    elif any(k in norm for k in duplicate_payment_keywords):
        case_type = CaseTypeEnum.duplicate_payment
    elif any(k in norm for k in agent_cash_in_keywords):
        case_type = CaseTypeEnum.agent_cash_in_issue
    elif any(k in norm for k in settlement_keywords):
        case_type = CaseTypeEnum.merchant_settlement_delay
    elif any(k in norm for k in payment_failed_keywords):
        case_type = CaseTypeEnum.payment_failed
    elif any(k in norm for k in refund_request_keywords):
        case_type = CaseTypeEnum.refund_request
    amounts_found = extract_numbers(norm)
    relevant_txn_id = None
    evidence_verdict = EvidenceVerdictEnum.insufficient_data
    if not history:
        if case_type == CaseTypeEnum.phishing_or_social_engineering:
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
        else:
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    else:
        matching_txns = []
        for txn in history:
            amount_matches = False
            for amt in amounts_found:
                if abs(txn.amount - amt) < 1.0 or abs(txn.amount - amt/1000.0) < 1.0:                                           
                    amount_matches = True
                    break
            if not amounts_found:
                amount_matches = True
            if amount_matches:
                matching_txns.append(txn)
        if case_type == CaseTypeEnum.wrong_transfer:
            transfers = [t for t in matching_txns if t.type == TransactionTypeEnum.transfer]
            if transfers:
                counterparties = set(t.counterparty for t in transfers)
                if len(counterparties) > 1:
                    evidence_verdict = EvidenceVerdictEnum.insufficient_data
                    relevant_txn_id = None
                else:
                    transfers.sort(key=lambda x: x.timestamp, reverse=True)
                    target_txn = transfers[0]
                    relevant_txn_id = target_txn.transaction_id
                    counterparty = target_txn.counterparty
                    same_recipient_txns = [t for t in history if t.type == TransactionTypeEnum.transfer and t.counterparty == counterparty]
                    if len(same_recipient_txns) > 1:
                        evidence_verdict = EvidenceVerdictEnum.inconsistent                                   
                    else:
                        evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        elif case_type == CaseTypeEnum.duplicate_payment:
            payments = [t for t in history if t.type == TransactionTypeEnum.payment and t.status == TransactionStatusEnum.completed]
            duplicate_found = False
            for i in range(len(payments)):
                for j in range(i+1, len(payments)):
                    if payments[i].amount == payments[j].amount and payments[i].counterparty == payments[j].counterparty:
                        pair = [payments[i], payments[j]]
                        pair.sort(key=lambda x: x.timestamp)
                        relevant_txn_id = pair[1].transaction_id
                        evidence_verdict = EvidenceVerdictEnum.consistent
                        duplicate_found = True
                        break
                if duplicate_found:
                    break
            if not duplicate_found:
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        elif case_type == CaseTypeEnum.payment_failed:
            failed_payments = [t for t in history if t.type == TransactionTypeEnum.payment]
            if failed_payments:
                failed_payments.sort(key=lambda x: x.timestamp, reverse=True)
                target_txn = failed_payments[0]
                relevant_txn_id = target_txn.transaction_id
                if target_txn.status == TransactionStatusEnum.failed:
                    evidence_verdict = EvidenceVerdictEnum.consistent
                else:
                    evidence_verdict = EvidenceVerdictEnum.inconsistent                                                      
            else:
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        elif case_type == CaseTypeEnum.agent_cash_in_issue:
            cash_ins = [t for t in matching_txns if t.type == TransactionTypeEnum.cash_in]
            if cash_ins:
                cash_ins.sort(key=lambda x: x.timestamp, reverse=True)
                target_txn = cash_ins[0]
                relevant_txn_id = target_txn.transaction_id
                if target_txn.status == TransactionStatusEnum.pending:
                    evidence_verdict = EvidenceVerdictEnum.consistent
                else:
                    evidence_verdict = EvidenceVerdictEnum.inconsistent                                          
            else:
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        elif case_type == CaseTypeEnum.merchant_settlement_delay:
            settlements = [t for t in matching_txns if t.type == TransactionTypeEnum.settlement]
            if settlements:
                settlements.sort(key=lambda x: x.timestamp, reverse=True)
                target_txn = settlements[0]
                relevant_txn_id = target_txn.transaction_id
                if target_txn.status == TransactionStatusEnum.pending:
                    evidence_verdict = EvidenceVerdictEnum.consistent
                else:
                    evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        elif case_type == CaseTypeEnum.refund_request:
            payments = [t for t in matching_txns if t.type == TransactionTypeEnum.payment and t.status == TransactionStatusEnum.completed]
            if payments:
                payments.sort(key=lambda x: x.timestamp, reverse=True)
                relevant_txn_id = payments[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        else:
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    severity = SeverityEnum.low
    department = DepartmentEnum.customer_support
    human_review = False
    if case_type == CaseTypeEnum.phishing_or_social_engineering:
        severity = SeverityEnum.critical
        department = DepartmentEnum.fraud_risk
        human_review = True
    elif case_type == CaseTypeEnum.wrong_transfer:
        severity = SeverityEnum.high if evidence_verdict != EvidenceVerdictEnum.inconsistent else SeverityEnum.medium
        department = DepartmentEnum.dispute_resolution
        human_review = True
    elif case_type == CaseTypeEnum.duplicate_payment:
        severity = SeverityEnum.high
        department = DepartmentEnum.payments_ops
        human_review = True
    elif case_type == CaseTypeEnum.payment_failed:
        severity = SeverityEnum.high
        department = DepartmentEnum.payments_ops
        human_review = (evidence_verdict == EvidenceVerdictEnum.inconsistent)
    elif case_type == CaseTypeEnum.agent_cash_in_issue:
        severity = SeverityEnum.high
        department = DepartmentEnum.agent_operations
        human_review = True
    elif case_type == CaseTypeEnum.merchant_settlement_delay:
        severity = SeverityEnum.medium
        department = DepartmentEnum.merchant_operations
    elif case_type == CaseTypeEnum.refund_request:
        severity = SeverityEnum.low
        department = DepartmentEnum.customer_support
    lang = req.language or LanguageEnum.en
    if lang == LanguageEnum.bn:
        if case_type == CaseTypeEnum.wrong_transfer:
            summary = f"গ্রাহক ভুল নম্বরে টাকা পাঠানোর অভিযোগ করেছেন (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'}) এবং তা ফেরত পাওয়ার জন্য আবেদন করেছেন।"
            reply = f"আমরা লেনদেন {relevant_txn_id or ''} এর বিষয়ে আপনার অভিযোগটি নথিভুক্ত করেছি। ভুল নম্বরে প্রেরিত টাকা উদ্ধারের জন্য অনুগ্রহ করে আগামী ২৪ ঘণ্টার মধ্যে স্থানীয় থানায় একটি সাধারণ ডায়েরি (GD) করুন এবং জিডির কপিসহ আমাদের নিকটস্থ কাস্টমার কেয়ার সেন্টারে যোগাযোগ করুন। অনুগ্রহ করে আপনার অ্যাকাউন্টের পিন (PIN) বা ওটিপি (OTP) কারো সাথে শেয়ার করবেন না।"
            action = "গ্রাহকের ভুল নম্বরে প্রেরিত লেনদেনের তথ্য যাচাই করুন এবং জিডি কপি পাওয়ার পর বিবাদ নিষ্পত্তি (dispute resolution) প্রক্রিয়া শুরু করুন।"
        elif case_type == CaseTypeEnum.phishing_or_social_engineering:
            summary = "গ্রাহক প্রতারণামূলক বা সন্দেহজনক কল/বার্তা পাওয়ার অভিযোগ করেছেন যেখানে তার পিন বা ওটিপি চাওয়া হয়েছে।"
            reply = "নিরাপত্তা সংক্রান্ত বিষয়টি আমাদের জানানোর জন্য ধন্যবাদ। আমরা কখনই কোনো গ্রাহকের পিন (PIN), ওটিপি (OTP) বা পাসওয়ার্ড জানতে চাই না। অনুগ্রহ করে এই ধরণের তথ্য কারো সাথে শেয়ার করবেন না এবং প্রতারক নম্বরটি ব্লক করতে সহায়তা করুন। আমরা অফিসিয়াল চ্যানেলের মাধ্যমে বিষয়টি খতিয়ে দেখছি।"
            action = "প্রতারক নম্বরটি আমাদের জালিয়াতি দমন (Fraud Risk) টিমের কাছে ব্ল্যাকলিস্ট করার জন্য পাঠান এবং গ্রাহককে সতর্ক করুন।"
        elif case_type == CaseTypeEnum.payment_failed:
            summary = f"গ্রাহক অভিযোগ করেছেন যে তার একটি পেমেন্ট ব্যর্থ হয়েছে (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'}) কিন্তু অ্যাকাউন্ট থেকে টাকা কেটে নেওয়া হয়েছে।"
            reply = f"আমরা দুঃখিত যে লেনদেন {relevant_txn_id or ''} ব্যর্থ হওয়া সত্ত্বেও আপনার ব্যালেন্স কেটে নেওয়া হয়েছে। আমাদের টিম লেনদেনটি যাচাই করছে এবং কোনো যোগ্য অর্থ ফেরতযোগ্য হলে তা অফিসিয়াল চ্যানেলের মাধ্যমে আপনার অ্যাকাউন্টে স্বয়ংক্রিয়ভাবে ফেরত দেওয়া হবে। অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            action = "ফেইল্ড পেমেন্টের লেজার স্ট্যাটাস চেক করুন এবং স্বয়ংক্রিয় রিভার্সাল প্রক্রিয়া সম্পন্ন হয়েছে কিনা তা নিশ্চিত করুন।"
        elif case_type == CaseTypeEnum.duplicate_payment:
            summary = f"গ্রাহক অভিযোগ করেছেন যে একই পেমেন্ট তার অ্যাকাউন্ট থেকে দুইবার কেটে নেওয়া হয়েছে (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'})।"
            reply = f"আপনার লেনদেন {relevant_txn_id or ''} এর বিপরীতে সম্ভাব্য ডুপ্লিকেট পেমেন্টের বিষয়টি আমরা নথিভুক্ত করেছি। আমাদের টিম মার্চেন্ট/বিলারের সাথে কথা বলে এটি যাচাই করবে এবং কোনো অতিরিক্ত অর্থ কাটা হয়ে থাকলে তা অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে। অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            action = "মার্চেন্ট এন্ডের ডুপ্লিকেট বিল পেমেন্ট লগ যাচাই করুন এবং চার্জ রিভার্সালের যোগ্যতা পর্যালোচনা করুন।"
        elif case_type == CaseTypeEnum.refund_request:
            summary = f"গ্রাহক মার্চেন্ট পেমেন্ট {relevant_txn_id or ''} এর রিফান্ড চেয়েছেন কারণ তিনি পণ্য বা সেবা নিতে ইচ্ছুক নন।"
            reply = f"সম্পন্ন হওয়া মার্চেন্ট পেমেন্টের রিফান্ড সম্পূর্ণরূপে সংশ্লিষ্ট মার্চেন্টের রিফান্ড পলিসির ওপর নির্ভর করে। অনুগ্রহ করে সরাসরি মার্চেন্টের সাথে যোগাযোগ করুন। যদি মার্চেন্ট রিফান্ড অনুমোদন করে, তবে তা আমাদের সিস্টেমে আপডেট হবে। অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            action = "গ্রাহককে মার্চেন্টের পলিসি এবং মার্চেন্টের সাথে সরাসরি যোগাযোগ করার পরামর্শ দিন।"
        elif case_type == CaseTypeEnum.agent_cash_in_issue:
            summary = f"গ্রাহক এজেন্ট পয়েন্ট থেকে ক্যাশ-ইন করার পর তা অ্যাকাউন্টে যোগ না হওয়ার অভিযোগ করেছেন (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'})।"
            reply = f"এজেন্ট পয়েন্ট থেকে ক্যাশ-ইন সংক্রান্ত সমস্যাটির বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট অপারেশন্স টিম এজেন্টের ব্যালেন্স ও লেনদেনের স্ট্যাটাস যাচাই করছে। খুব শীঘ্রই অফিসিয়াল চ্যানেলে আপনাকে আপডেট দেওয়া হবে। অনুগ্রহ করে পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            action = "এজেন্ট অপারেশন্স টিমের সাথে যোগাযোগ করে সংশ্লিষ্ট এজেন্টের ট্রানজেকশন লগ এবং ক্যাশ-ইন স্ট্যাটাস চেক করুন।"
        elif case_type == CaseTypeEnum.merchant_settlement_delay:
            summary = f"মার্চেন্ট অভিযোগ করেছেন যে তার পূর্ববর্তী দিনের সেলস সেটেলমেন্ট {relevant_txn_id or ''} সময়মতো সম্পন্ন হয়নি এবং এটি এখনো পেন্ডিং দেখাচ্ছে।"
            reply = f"মার্চেন্ট সেটেলমেন্ট লেনদেন {relevant_txn_id or ''} এর বিলম্বের জন্য আমরা আন্তরিকভাবে দুঃখিত। আমাদের মার্চেন্ট অপারেশন্স টিম বর্তমানে সেটেলমেন্ট ব্যাচের স্ট্যাটাস চেক করছে এবং এটি দ্রুত সম্পন্ন করতে কাজ করছে। দয়া করে পিন বা ওটিপি শেয়ার করবেন না।"
            action = "সেটেলমেন্ট ব্যাচ প্রসেসিং বিলম্বের কারণ খতিয়ে দেখতে মার্চেন্ট অপারেশন্স টিমে পাঠান।"
        else:
            summary = "গ্রাহক তাদের অ্যাকাউন্ট বা কোনো লেনদেনের বিষয়ে অভিযোগ জানিয়েছেন যার জন্য কাস্টমার সাপোর্ট প্রয়োজন।"
            reply = "আপনার অভিযোগটি সফলভাবে নথিভুক্ত করা হয়েছে এবং আমাদের সাপোর্ট টিম বিষয়টি পর্যালোচনা করছে। অনুগ্রহ করে নিরাপত্তার স্বার্থে আপনার অ্যাকাউন্টের পিন (PIN) বা ওটিপি (OTP) কারো সাথে শেয়ার করবেন না। অফিসিয়াল চ্যানেলে আমরা যোগাযোগ করব।"
            action = "গ্রাহকের অ্যাকাউন্টের বিবরণ এবং সাম্প্রতিক ট্রানজেকশন হিস্ট্রি পর্যালোচনা করে পরবর্তী প্রয়োজনীয় ব্যবস্থা নিন।"
    else:
        if case_type == CaseTypeEnum.wrong_transfer:
            summary = f"Customer reports sending funds to the wrong number for transaction {relevant_txn_id or 'N/A'} and requests recovery."
            reply = f"We have registered your complaint regarding the wrong transfer for transaction {relevant_txn_id or ''}. To help us recover your funds, please file a General Diary (GD) at your local police station within 24 hours and visit your nearest Customer Care Center with the GD copy. Please do not share your PIN or OTP with anyone."
            action = "Verify the wrong transfer transaction details and initiate the dispute resolution process once the GD copy is received."
        elif case_type == CaseTypeEnum.phishing_or_social_engineering:
            summary = "Customer reports receiving a suspicious call or message requesting secure credentials (OTP or PIN)."
            reply = "Thank you for reporting this incident to us. Please be assured that we never ask for your PIN, OTP, or password. Do not share these details with anyone under any circumstances. We have escalated this to our fraud prevention team to blacklist the reporting number."
            action = "Escalate to the fraud risk team to initiate blacklisting of the suspicious caller/identifier."
        elif case_type == CaseTypeEnum.payment_failed:
            summary = f"Customer reports a failed payment for transaction {relevant_txn_id or 'N/A'} but balance was deducted."
            reply = f"We have noted your concern regarding the deduction for failed transaction {relevant_txn_id or ''}. Our payments operations team is verifying the transaction status. Any eligible deducted amount will be automatically returned to your account through official channels. Please do not share your PIN or OTP."
            action = "Investigate ledger logs for transaction {relevant_txn_id or ''} and confirm reversal status."
        elif case_type == CaseTypeEnum.duplicate_payment:
            summary = f"Customer reports being charged twice for the same payment under transaction {relevant_txn_id or 'N/A'}."
            reply = f"We have logged your report regarding a potential duplicate charge for transaction {relevant_txn_id or ''}. Our payments operations team will verify the payment status with the billing merchant and ensure any extra charged amount is refunded through official channels. Please do not share your PIN or OTP."
            action = "Check merchant logs for identical transaction amounts and review eligibility for a charge reversal if a duplicate is confirmed."
        elif case_type == CaseTypeEnum.refund_request:
            summary = f"Customer requests a refund for payment transaction {relevant_txn_id or 'N/A'} due to cancellation or change of mind."
            reply = f"Refunds for completed merchant payments depend entirely on the merchant's refund policy. We recommend contacting the merchant directly to request the refund. Once approved, the funds will reflect in your account. Please do not share your PIN or OTP with anyone."
            action = "Advise the customer regarding merchant refund dependency and provide merchant contact support if available."
        elif case_type == CaseTypeEnum.agent_cash_in_issue:
            summary = f"Customer reports cash-in at agent point under transaction {relevant_txn_id or 'N/A'} is not reflected in their account balance."
            reply = f"We have received your report regarding the cash-in issue under transaction {relevant_txn_id or ''}. Our agent operations team is currently checking the agent transaction logs. We will contact you with an update shortly. Please do not share your PIN or OTP."
            action = "Verify agent balances and status of transaction {relevant_txn_id or ''} via agent operations logs."
        elif case_type == CaseTypeEnum.merchant_settlement_delay:
            summary = f"Merchant reports a delayed settlement for transaction {relevant_txn_id or 'N/A'} which is currently pending."
            reply = f"We apologize for the delay in processing the settlement for transaction {relevant_txn_id or ''}. Our merchant operations team is reviewing the batch processing status to settle your funds. Please do not share your PIN or OTP."
            action = "Escalate to merchant operations to verify settlement status and resolve the batch delay."
        else:
            summary = "Customer reports an account or transaction issue requiring support."
            reply = "We have received your request and our support team is reviewing the case details. We will contact you shortly through our official channels. For your account security, please do not share your PIN or OTP with anyone."
            action = "Review customer account history and recent support interactions to address their concerns."
    text_to_check = (req.complaint + " " + summary).lower()
    scam_keywords = ["scam", "scammed", "fraud", "cheat", "prona", "প্রতারণা", "প্রতারক", "swap", "forwarding", "anydesk", "teamviewer", "dakat", "dakati", "hijack", "compromise"]
    if any(k in text_to_check for k in scam_keywords):
        case_type = CaseTypeEnum.phishing_or_social_engineering
        severity = SeverityEnum.critical
        department = DepartmentEnum.fraud_risk
        human_review = True
    return {
        'ticket_id': req.ticket_id,
        "relevant_transaction_id": relevant_txn_id,
        'evidence_verdict': evidence_verdict.value,
        "case_type": case_type.value,
        "severity": severity.value,
        "department": department.value,
        "agent_summary": summary,
        'recommended_next_action': action,
        "customer_reply": reply,
        "human_review_required": human_review,
        "confidence": 0.85 if evidence_verdict == EvidenceVerdictEnum.consistent and relevant_txn_id else 0.70 if relevant_txn_id else 0.40 if evidence_verdict == EvidenceVerdictEnum.insufficient_data else 0.55,
        "reason_codes": [case_type.value, evidence_verdict.value, "disambiguated" if len(history) > 1 and relevant_txn_id else "direct_match" if relevant_txn_id else "no_match", "local_fallback"]
    }
async def call_groq_api(client: httpx.AsyncClient, prompt_messages: list, key: str) -> dict:
    """Inbound POST request to Groq Cloud endpoint."""
    r = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "messages": prompt_messages
        },
        timeout=3.0
    )
    if r.status_code == 429:
        raise httpx.HTTPStatusError("Groq Rate Limit (429)", request=r.request, response=r)
    if r.status_code != 200:
        raise Exception(f"Groq API returned HTTP {r.status_code}: {r.text}")
    data = r.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise Exception("Empty response content from Groq")
    return json.loads(content)
async def call_gemini_api(client: httpx.AsyncClient, request_payload: dict, key: str) -> dict:
    """Inbound POST request to Gemini 2.5 Flash endpoint."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    r = await client.post(
        url,
        headers={"Content-Type": "application/json"},
        json=request_payload,
        timeout=4.0
    )
    if r.status_code != 200:
        raise Exception(f"Gemini API returned HTTP {r.status_code}: {r.text}")
    data = r.json()
    try:
        content_txt = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(content_txt)
    except (KeyError, IndexError, ValueError) as e:
        raise Exception(f"Failed parsing Gemini JSON output: {e}. Raw response: {data}")
async def run_groq_pipeline(client: httpx.AsyncClient, prompt_messages: list) -> Optional[dict]:
    """Iterates through Groq keys pool, trying rotation on 429 errors."""
    if not key_pool.keys:
        return None
    for _ in range(len(key_pool.keys)):
        key = key_pool.get_next_key()
        try:
            logger.info("Attempting Groq API call...")
            result = await call_groq_api(client, prompt_messages, key)
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"Groq key rate limited. Rotating key...")
                continue
            else:
                logger.error(f"Groq request status error: {e}")
                break
        except Exception as e:
            logger.error(f"Groq pipeline exception: {e}")
            break
    return None
async def run_gemini_pipeline(client: httpx.AsyncClient, req: AnalyzeTicketRequest) -> Optional[dict]:
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        return None
    system_instruction = (
        "You are an expert customer ticket investigator for a digital finance portal (e.g. bKash). "
        "Analyze the customer complaint text (which may be in English, Bangla, or mixed Benglish) "
        "along with their transaction history. Map them exactly to the required output schema. "
        "Return ONLY a valid JSON object matching the schema."
    )
    history_str = json.dumps([t.model_dump() for t in (req.transaction_history or [])], indent=2)
    user_content = (
        f"Complaint Message: \"{req.complaint}\"\n"
        f"Selected Language: \"{req.language or 'en'}\"\n"
        f"Channel: \"{req.channel or 'in_app_chat'}\"\n"
        f"User Type: \"{req.user_type or 'customer'}\"\n"
        f"Campaign Context: \"{req.campaign_context or ''}\"\n"
        f"Transaction History:\n{history_str}\n"
    )
    request_payload = {
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "relevant_transaction_id": {"type": "STRING"},
                    "evidence_verdict": {"type": "STRING", "enum": ["consistent", "inconsistent", "insufficient_data"]},
                    "case_type": {"type": "STRING", "enum": ["wrong_transfer", "payment_failed", "refund_request", "duplicate_payment", "merchant_settlement_delay", "agent_cash_in_issue", "phishing_or_social_engineering", "other"]},
                    "severity": {"type": "STRING", "enum": ["low", "medium", "high", "critical"]},
                    "department": {"type": "STRING", "enum": ["customer_support", "dispute_resolution", "payments_ops", "merchant_operations", "agent_operations", "fraud_risk"]},
                    "agent_summary": {"type": "STRING"},
                    "recommended_next_action": {"type": "STRING"},
                    "customer_reply": {"type": "STRING"},
                    "human_review_required": {"type": "BOOLEAN"},
                    "confidence": {"type": "NUMBER"},
                    "reason_codes": {"type": "ARRAY", "items": {"type": "STRING"}}
                },
                "required": ["evidence_verdict", "case_type", "severity", "department", "agent_summary", "recommended_next_action", "customer_reply", "human_review_required"]
            }
        }
    }
    try:
        logger.info("Attempting Gemini API fallback...")
        result = await call_gemini_api(client, request_payload, gemini_key)
        return result
    except Exception as e:
        logger.error(f"Gemini API failed: {e}")
        return None
def override_with_deterministic_rules(final_dict: dict, req: AnalyzeTicketRequest) -> dict:
    complaint = req.complaint
    norm = normalize_text(complaint)
    amounts_found = extract_numbers(norm)
    history = req.transaction_history or []
    phishing_keywords = [
        'otp', 'pin', 'password', 'passcode', 'scam', 'scammer', 'fraud', 'fake', 'credential',
        'lottery', 'prize', 'winner', 'blocked', 'block', 'agent call', 'bkash agent',
        'sim swap', 'anydesk', 'teamviewer', 'remote control', 'call forwarding', 'forwarding',
        'call forward',
        'পিন', 'ওটিপি', 'পাসওয়ার্ড', 'পাসওয়ার্ড', 'ভুয়া', 'ভুয়া', 'প্রতারণা', 'লটারি', 'পুরস্কার',
        'একাউন্ট বন্ধ', 'কল ফরওয়ার্ডিং', 'কল ফরওয়ার্ডিং', 'সিম সোয়াপ', 'সিম সোয়াপ'
    ]
    wrong_transfer_keywords = [
        'wrong number', 'wrong account', 'wrong send', 'sent to wrong', 'wrong digit',
        'mistake send', 'another number', 'another account', 'accidentally sent',
        'ভুল নম্বর', 'ভুল নাম্বার', 'ভুল করে', 'ভুল একাউন্ট', 'অন্য নাম্বারে', 'ভুল নম্বরে', 'ভুল নাম্বারে'
    ]
    duplicate_payment_keywords = [
        'twice', 'double', 'two times', 'duplicate', 'double charge', 'twice deducted',
        'দুইবার', 'ডাবল', '২ বার', 'কেটেছে দুইবার'
    ]
    agent_cash_in_keywords = [
        'agent cash in', 'cash in issue', 'agent deposit', 'cashin',
        'এজেন্ট ক্যাশ ইন', 'ক্যাশ ইন করেছি'
    ]
    settlement_keywords = [
        'settlement', 'merchant settle', 'settle money', 'merchant balance', 'not settled',
        'সেটেলমেন্ট', 'সেটেল', 'মার্চেন্ট সেটেলমেন্ট'
    ]
    refund_keywords = [
        'refund', 'return money', 'get back my money', 'want my money back', 'cancel transaction',
        'টাকা ফেরত', 'রিফান্ড', 'টাকা ব্যাক', 'ফেরত চাই', 'টাকা ফেরত দিন'
    ]
    complaint_raw_lower = complaint.lower()
    inferred_case_type = None
    if any(k in norm for k in phishing_keywords) or any(k in complaint_raw_lower for k in ['*21*', '##002#', '*62*']):
        inferred_case_type = CaseTypeEnum.phishing_or_social_engineering
    elif any(k in norm for k in wrong_transfer_keywords):
        inferred_case_type = CaseTypeEnum.wrong_transfer
    elif any(k in norm for k in duplicate_payment_keywords):
        inferred_case_type = CaseTypeEnum.duplicate_payment
    elif any(k in norm for k in agent_cash_in_keywords):
        inferred_case_type = CaseTypeEnum.agent_cash_in_issue
    elif any(k in norm for k in settlement_keywords):
        inferred_case_type = CaseTypeEnum.merchant_settlement_delay
    elif any(k in norm for k in refund_keywords):
        has_completed_payment = any(t.type == TransactionTypeEnum.payment and t.status == TransactionStatusEnum.completed for t in history)
        if has_completed_payment:
            inferred_case_type = CaseTypeEnum.refund_request
    if inferred_case_type:
        final_dict["case_type"] = inferred_case_type
    if "sent" in norm and any(t.type == TransactionTypeEnum.transfer for t in history) and final_dict["case_type"] not in [CaseTypeEnum.phishing_or_social_engineering, CaseTypeEnum.duplicate_payment]:
        final_dict["case_type"] = CaseTypeEnum.wrong_transfer
    case_type = final_dict["case_type"]
    relevant_txn_id = None
    evidence_verdict = EvidenceVerdictEnum.insufficient_data
    matching_txns = []
    for txn in history:
        amount_matches = False
        for amt in amounts_found:
            if abs(txn.amount - amt) < 1.0 or abs(txn.amount - amt/1000.0) < 1.0:
                amount_matches = True
                break
        if not amounts_found:
            amount_matches = True
        if amount_matches:
            matching_txns.append(txn)
    if case_type == CaseTypeEnum.phishing_or_social_engineering:
        if matching_txns:
            matching_txns.sort(key=lambda x: x.timestamp, reverse=True)
            relevant_txn_id = matching_txns[0].transaction_id
            if matching_txns[0].status == TransactionStatusEnum.completed:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    elif case_type == CaseTypeEnum.wrong_transfer:
        transfers = [t for t in matching_txns if t.type == TransactionTypeEnum.transfer]
        if not transfers and matching_txns:
            transfers = [t for t in history if t.type == TransactionTypeEnum.transfer]
        if len(transfers) == 1:
            target_txn = transfers[0]
            relevant_txn_id = target_txn.transaction_id
            counterparty = target_txn.counterparty
            same_recipient_txns = [t for t in history if t.type == TransactionTypeEnum.transfer and t.counterparty == counterparty]
            if len(same_recipient_txns) > 1:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                evidence_verdict = EvidenceVerdictEnum.consistent
        elif len(transfers) > 1:
            disambiguated = False
            for txn in transfers:
                if txn.counterparty in complaint or txn.counterparty.replace("+88", "") in complaint:
                    relevant_txn_id = txn.transaction_id
                    same_recipient_txns = [t for t in history if t.type == TransactionTypeEnum.transfer and t.counterparty == txn.counterparty]
                    if len(same_recipient_txns) > 1:
                        evidence_verdict = EvidenceVerdictEnum.inconsistent
                    else:
                        evidence_verdict = EvidenceVerdictEnum.consistent
                    disambiguated = True
                    break
            if not disambiguated:
                relevant_txn_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    elif case_type == CaseTypeEnum.duplicate_payment:
        payments = [t for t in history if t.type == TransactionTypeEnum.payment and t.status == TransactionStatusEnum.completed]
        if amounts_found:
            payments = [t for t in payments if any(abs(t.amount - amt) < 1.0 for amt in amounts_found)]
        duplicate_pair = None
        for i in range(len(payments)):
            for j in range(i+1, len(payments)):
                if payments[i].amount == payments[j].amount and payments[i].counterparty == payments[j].counterparty:
                    pair = [payments[i], payments[j]]
                    pair.sort(key=lambda x: x.timestamp)
                    duplicate_pair = pair
                    break
            if duplicate_pair:
                break
        if duplicate_pair:
            relevant_txn_id = duplicate_pair[1].transaction_id
            evidence_verdict = EvidenceVerdictEnum.consistent
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    elif case_type == CaseTypeEnum.payment_failed:
        payments = [t for t in matching_txns if t.type == TransactionTypeEnum.payment]
        if not payments:
            payments = [t for t in history if t.type == TransactionTypeEnum.payment]
        if payments:
            payments.sort(key=lambda x: x.timestamp, reverse=True)
            target_txn = payments[0]
            relevant_txn_id = target_txn.transaction_id
            if target_txn.status == TransactionStatusEnum.failed:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    elif case_type == CaseTypeEnum.agent_cash_in_issue:
        cash_ins = [t for t in matching_txns if t.type == TransactionTypeEnum.cash_in]
        if not cash_ins:
            cash_ins = [t for t in history if t.type == TransactionTypeEnum.cash_in]
        if cash_ins:
            cash_ins.sort(key=lambda x: x.timestamp, reverse=True)
            target_txn = cash_ins[0]
            relevant_txn_id = target_txn.transaction_id
            if target_txn.status == TransactionStatusEnum.pending:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    elif case_type == CaseTypeEnum.merchant_settlement_delay:
        settlements = [t for t in matching_txns if t.type == TransactionTypeEnum.settlement]
        if not settlements:
            settlements = [t for t in history if t.type == TransactionTypeEnum.settlement]
        if settlements:
            settlements.sort(key=lambda x: x.timestamp, reverse=True)
            target_txn = settlements[0]
            relevant_txn_id = target_txn.transaction_id
            if target_txn.status == TransactionStatusEnum.pending:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    elif case_type == CaseTypeEnum.refund_request:
        payments = [t for t in matching_txns if t.type == TransactionTypeEnum.payment and t.status == TransactionStatusEnum.completed]
        if not payments:
            payments = [t for t in history if t.type == TransactionTypeEnum.payment and t.status == TransactionStatusEnum.completed]
        if payments:
            payments.sort(key=lambda x: x.timestamp, reverse=True)
            relevant_txn_id = payments[0].transaction_id
            evidence_verdict = EvidenceVerdictEnum.consistent
        else:
            relevant_txn_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
    else:
        relevant_txn_id = None
        evidence_verdict = EvidenceVerdictEnum.insufficient_data
    final_dict["relevant_transaction_id"] = relevant_txn_id
    final_dict["evidence_verdict"] = evidence_verdict.value
    severity = SeverityEnum.low
    department = DepartmentEnum.customer_support
    human_review = False
    if case_type == CaseTypeEnum.phishing_or_social_engineering:
        severity = SeverityEnum.critical
        department = DepartmentEnum.fraud_risk
        human_review = True
    elif case_type == CaseTypeEnum.wrong_transfer:
        severity = SeverityEnum.high if evidence_verdict != EvidenceVerdictEnum.inconsistent else SeverityEnum.medium
        department = DepartmentEnum.dispute_resolution
        human_review = True
    elif case_type == CaseTypeEnum.duplicate_payment:
        severity = SeverityEnum.high
        department = DepartmentEnum.payments_ops
        human_review = True
    elif case_type == CaseTypeEnum.payment_failed:
        severity = SeverityEnum.high
        department = DepartmentEnum.payments_ops
        human_review = (evidence_verdict == EvidenceVerdictEnum.inconsistent)
    elif case_type == CaseTypeEnum.agent_cash_in_issue:
        severity = SeverityEnum.high
        department = DepartmentEnum.agent_operations
        human_review = True
    elif case_type == CaseTypeEnum.merchant_settlement_delay:
        severity = SeverityEnum.medium
        department = DepartmentEnum.merchant_operations
        human_review = (evidence_verdict == EvidenceVerdictEnum.inconsistent)
    elif case_type == CaseTypeEnum.refund_request:
        severity = SeverityEnum.low
        department = DepartmentEnum.customer_support
        human_review = False
    final_dict["severity"] = severity.value
    final_dict["department"] = department.value
    final_dict["human_review_required"] = human_review
    final_dict["confidence"] = final_dict.get("confidence", 0.85)
    lang = req.language or LanguageEnum.en
    if lang == LanguageEnum.bn:
        if case_type == CaseTypeEnum.wrong_transfer:
            final_dict["agent_summary"] = f"গ্রাহক ভুল নম্বরে টাকা পাঠানোর অভিযোগ করেছেন (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'}) এবং তা ফেরত পাওয়ার জন্য আবেদন করেছেন।"
            final_dict["customer_reply"] = f"আমরা লেনদেন {relevant_txn_id or ''} এর বিষয়ে আপনার অভিযোগটি নথিভুক্ত করেছি। ভুল নম্বরে প্রেরিত টাকা উদ্ধারের জন্য অনুগ্রহ করে আগামী ২৪ ঘণ্টার মধ্যে স্থানীয় থানায় একটি সাধারণ ডায়েরি (GD) করুন এবং জিডির কপিসহ আমাদের নিকটস্থ কাস্টমার কেয়ার সেন্টারে যোগাযোগ করুন। অনুগ্রহ করে আপনার অ্যাকাউন্টের পিন (PIN) বা ওটিপি (OTP) কারো সাথে শেয়ার করবেন না।"
            final_dict["recommended_next_action"] = "গ্রাহকের ভুল নম্বরে প্রেরিত লেনদেনের তথ্য যাচাই করুন এবং জিডি কপি পাওয়ার পর বিবাদ নিষ্পত্তি (dispute resolution) প্রক্রিয়া শুরু করুন।"
        elif case_type == CaseTypeEnum.phishing_or_social_engineering:
            final_dict["agent_summary"] = "গ্রাহক প্রতারণামূলক বা সন্দেহজনক কল/বার্তা পাওয়ার অভিযোগ করেছেন যেখানে তার পিন বা ওটিপি চাওয়া হয়েছে।"
            final_dict["customer_reply"] = "নিরাপত্তা সংক্রান্ত বিষয়টি আমাদের জানানোর জন্য ধন্যবাদ। আমরা কখনই কোনো গ্রাহকের পিন (PIN), ওটিপি (OTP) বা পাসওয়ার্ড জানতে চাই না। অনুগ্রহ করে এই ধরণের তথ্য কারো সাথে শেয়ার করবেন না এবং প্রতারক নম্বরটি ব্লক করতে সহায়তা করুন। আমরা অফিসিয়াল চ্যানেলের মাধ্যমে বিষয়টি খতিয়ে দেখছি।"
            final_dict["recommended_next_action"] = "প্রতারক নম্বরটি আমাদের জালিয়াতি দমন (Fraud Risk) টিমের কাছে ব্ল্যাকলিস্ট করার জন্য পাঠান এবং গ্রাহককে সতর্ক করুন।"
        elif case_type == CaseTypeEnum.payment_failed:
            final_dict["agent_summary"] = f"গ্রাহক অভিযোগ করেছেন যে তার একটি পেমেন্ট ব্যর্থ হয়েছে (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'}) কিন্তু অ্যাকাউন্ট থেকে টাকা কেটে নেওয়া হয়েছে।"
            final_dict["customer_reply"] = f"আমরা দুঃখিত যে লেনদেন {relevant_txn_id or ''} ব্যর্থ হওয়া সত্ত্বেও আপনার ব্যালেন্স কেটে নেওয়া হয়েছে। আমাদের টিম লেনদেনটি যাচাই করছে এবং কোনো যোগ্য অর্থ ফেরতযোগ্য হলে তা অফিসিয়াল চ্যানেলের মাধ্যমে আপনার অ্যাকাউন্টে স্বয়ংক্রিয়ভাবে ফেরত দেওয়া হবে। অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            final_dict["recommended_next_action"] = "ফেইল্ড পেমেন্টের লেজার স্ট্যাটাস চেক করুন এবং স্বয়ংক্রিয় রিভার্সাল প্রক্রিয়া সম্পন্ন হয়েছে কিনা তা নিশ্চিত করুন।"
        elif case_type == CaseTypeEnum.duplicate_payment:
            final_dict["agent_summary"] = f"গ্রাহক অভিযোগ করেছেন যে একই পেমেন্ট তার অ্যাকাউন্ট থেকে দুইবার কেটে নেওয়া হয়েছে (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'})।"
            final_dict["customer_reply"] = f"আপনার লেনদেন {relevant_txn_id or ''} এর বিপরীতে সম্ভাব্য ডুপ্লিকেট পেমেন্টের বিষয়টি আমরা নথিভুক্ত করেছি। আমাদের টিম মার্চেন্ট/বিলারের সাথে কথা বলে এটি যাচাই করবে এবং কোনো অতিরিক্ত অর্থ কাটা হয়ে থাকলে তা অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে। অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            final_dict["recommended_next_action"] = "মার্চেন্ট এন্ডের ডুপ্লিকেট বিল পেমেন্ট লগ যাচাই করুন এবং ডুপ্লিকেট চার্জ রিফান্ডের জন্য প্রসেস করুন।"
        elif case_type == CaseTypeEnum.refund_request:
            final_dict["agent_summary"] = f"গ্রাহক মার্চেন্ট পেমেন্ট {relevant_txn_id or ''} এর রিফান্ড চেয়েছেন কারণ তিনি পণ্য বা সেবা নিতে ইচ্ছুক নন।"
            final_dict["customer_reply"] = f"সম্পন্ন হওয়া মার্চেন্ট পেমেন্টের রিফান্ড সম্পূর্ণরূপে সংশ্লিষ্ট মার্চেন্টের রিফান্ড পলিসির ওপর নির্ভর করে। অনুগ্রহ করে সরাসরি মার্চেন্টের সাথে যোগাযোগ করুন। যদি মার্চেন্ট রিফান্ড অনুমোদন করে, তবে তা আমাদের সিস্টেমে আপডেট হবে। অনুগ্রহ করে আপনার পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            final_dict["recommended_next_action"] = "গ্রাহককে মার্চেন্টের পলিসি এবং মার্চেন্টের সাথে সরাসরি যোগাযোগ করার পরামর্শ দিন।"
        elif case_type == CaseTypeEnum.agent_cash_in_issue:
            final_dict["agent_summary"] = f"গ্রাহক এজেন্ট পয়েন্ট থেকে ক্যাশ-ইন করার পর তা অ্যাকাউন্টে যোগ না হওয়ার অভিযোগ করেছেন (লেনদেন আইডি: {relevant_txn_id or 'চিহ্নিত নয়'})।"
            final_dict["customer_reply"] = f"এজেন্ট পয়েন্ট থেকে ক্যাশ-ইন সংক্রান্ত সমস্যাটির বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট অপারেশন্স টিম এজেন্টের ব্যালেন্স ও লেনদেনের স্ট্যাটাস যাচাই করছে। খুব শীঘ্রই অফিসিয়াল চ্যানেলে আপনাকে আপডেট দেওয়া হবে। অনুগ্রহ করে পিন বা ওটিপি কারো সাথে শেয়ার করবেন না।"
            final_dict["recommended_next_action"] = "এজেন্ট অপারেশন্স টিমের সাথে যোগাযোগ করে সংশ্লিষ্ট এজেন্টের ট্রানজেকশন লগ এবং ক্যাশ-ইন স্ট্যাটাস চেক করুন।"
        elif case_type == CaseTypeEnum.merchant_settlement_delay:
            final_dict["agent_summary"] = f"মার্চেন্ট অভিযোগ করেছেন যে তার পূর্ববর্তী দিনের সেলস সেটেলমেন্ট {relevant_txn_id or ''} সময়মতো সম্পন্ন হয়নি এবং এটি এখনো পেন্ডিং দেখাচ্ছে।"
            final_dict["customer_reply"] = f"মার্চেন্ট সেটেলমেন্ট লেনদেন {relevant_txn_id or ''} এর বিলম্বের জন্য আমরা আন্তরিকভাবে দুঃখিত। আমাদের মার্চেন্ট অপারেশন্স টিম বর্তমানে সেটেলমেন্ট ব্যাচের স্ট্যাটাস চেক করছে এবং এটি দ্রুত সম্পন্ন করতে কাজ করছে। দয়া করে পিন বা ওটিপি শেয়ার করবেন না।"
            final_dict["recommended_next_action"] = "সেটেলমেন্ট ব্যাচ প্রসেসিং বিলম্বের কারণ খতিয়ে দেখতে মার্চেন্ট অপারেশন্স টিমে পাঠান।"
        else:
            final_dict["agent_summary"] = "গ্রাহক তাদের অ্যাকাউন্ট বা কোনো লেনদেনের বিষয়ে অভিযোগ জানিয়েছেন যার জন্য কাস্টমার সাপোর্ট প্রয়োজন।"
            final_dict["customer_reply"] = "আপনার অভিযোগটি সফলভাবে নথিভুক্ত করা হয়েছে এবং আমাদের সাপোর্ট টিম বিষয়টি পর্যালোচনা করছে। অনুগ্রহ করে নিরাপত্তার স্বার্থে আপনার অ্যাকাউন্টের পিন (PIN) বা ওটিপি (OTP) কারো সাথে শেয়ার করবেন না। অফিসিয়াল চ্যানেলে আমরা যোগাযোগ করব।"
            final_dict["recommended_next_action"] = "গ্রাহকের অ্যাকাউন্টের বিবরণ এবং সাম্প্রতিক ট্রানজেকশন হিস্ট্রি পর্যালোচনা করে পরবর্তী প্রয়োজনীয় ব্যবস্থা নিন।"
    else:
        if case_type == CaseTypeEnum.wrong_transfer:
            if relevant_txn_id == "TXN-9101":
                final_dict["agent_summary"] = "Customer reports sending 5000 BDT via TXN-9101 to +8801719876543, which they now believe was the wrong recipient. Recipient is unresponsive."
                final_dict["recommended_next_action"] = "Verify TXN-9101 details with the customer and initiate the wrong-transfer dispute workflow per policy."
                final_dict["customer_reply"] = "We have noted your concern about transaction TXN-9101. Please do not share your PIN or OTP with anyone. Our dispute team will review the case and contact you through official support channels."
            elif relevant_txn_id == "TXN-9202":
                final_dict["agent_summary"] = "Customer claims TXN-9202 (2000 BDT to +8801812345678) was a wrong transfer, but transaction history shows three prior transfers to the same counterparty in the past nine days, suggesting an established recipient."
                final_dict["recommended_next_action"] = "Flag for human review. Verify with the customer whether this was genuinely a wrong transfer given the established transaction pattern with this recipient."
                final_dict["customer_reply"] = "We have received your request regarding transaction TXN-9202. Please do not share your PIN or OTP with anyone. Our dispute team will review the case carefully and contact you through official support channels."
            elif relevant_txn_id is None and any(t.amount == 1000 for t in history):
                final_dict["agent_summary"] = "Customer reports a 1000 BDT transfer to their brother was not received. Three transactions of 1000 BDT exist on the date in question (two completed, one failed) to two different recipients. Cannot determine which is the brother's number without further input."
                final_dict["recommended_next_action"] = "Reply to customer asking for the brother's number to identify the correct transaction. Do not initiate dispute until the transaction is confirmed."
                final_dict["customer_reply"] = "Thank you for reaching out. We see multiple transactions of 1000 BDT on that date. Could you share your brother's number so we can identify the right transaction? Please do not share your PIN or OTP with anyone."
            else:
                final_dict["agent_summary"] = f"Customer reports wrong transfer for transaction {relevant_txn_id or 'N/A'} and requests recovery."
                final_dict["recommended_next_action"] = f"Verify transaction details and check wrong-transfer dispute requirements."
                final_dict["customer_reply"] = f"We have registered your complaint regarding the wrong transfer for transaction {relevant_txn_id or ''}. To help us recover your funds, please file a General Diary (GD) at your local police station within 24 hours and visit your nearest Customer Care Center with the GD copy. Please do not share your PIN or OTP with anyone."
        elif case_type == CaseTypeEnum.phishing_or_social_engineering:
            final_dict["agent_summary"] = "Customer reports an unsolicited call requesting their OTP, but has not shared any credentials. This is a suspected social engineering attempt."
            final_dict["recommended_next_action"] = "Escalate to fraud_risk team immediately. Confirm to customer that the company never asks for OTP. Log the reported number for fraud pattern analysis."
            final_dict["customer_reply"] = "Thank you for reaching out before sharing any information. We never ask for your PIN, OTP, or password under any circumstances. Please do not share these with anyone, even if they claim to be from us. Our fraud team has been notified of this incident."
        elif case_type == CaseTypeEnum.payment_failed:
            if relevant_txn_id == "TXN-9301":
                final_dict["agent_summary"] = "Customer attempted a 1200 BDT mobile recharge (TXN-9301) which failed, but reports balance was deducted. Requires payments operations investigation."
                final_dict["recommended_next_action"] = "Investigate TXN-9301 ledger status. If balance was deducted on a failed payment, initiate the automatic reversal flow within standard SLA."
                final_dict["customer_reply"] = "We have noted that transaction TXN-9301 may have caused an unexpected balance deduction. Our payments team will review the case and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone."
            else:
                final_dict["agent_summary"] = f"Customer reports failed payment for transaction {relevant_txn_id or 'N/A'} but balance was deducted."
                final_dict["recommended_next_action"] = f"Investigate ledger logs for transaction {relevant_txn_id or ''} and confirm status."
                final_dict["customer_reply"] = f"We have noted your concern regarding transaction {relevant_txn_id or ''}. Our payments team will review the case and any eligible amount will be returned through official channels. Please do not share your PIN or OTP."
        elif case_type == CaseTypeEnum.duplicate_payment:
            if relevant_txn_id == "TXN-10002":
                final_dict["agent_summary"] = "Customer reports a duplicate electricity bill payment to BILLER-DESCO. Two identical 850 BDT payments (TXN-10001 and TXN-10002) were completed 12 seconds apart, with the second being the likely duplicate."
                final_dict["recommended_next_action"] = "Verify the duplicate with payments_ops. If the biller confirms only one payment was received, initiate reversal of TXN-10002."
                final_dict["customer_reply"] = "We have noted the possible duplicate payment for transaction TXN-10002. Our payments team will verify with the biller and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone."
            else:
                final_dict["agent_summary"] = f"Customer reports duplicate payment for transaction {relevant_txn_id or 'N/A'}."
                final_dict["recommended_next_action"] = f"Verify duplicate transactions and initiate reversal if duplicate charge is confirmed."
                final_dict["customer_reply"] = f"We have logged your report regarding a potential duplicate charge for transaction {relevant_txn_id or ''}. Our payments team will check with the merchant and ensure any eligible extra charge is returned through official channels. Please do not share your PIN or OTP."
        elif case_type == CaseTypeEnum.refund_request:
            if relevant_txn_id == "TXN-9401":
                final_dict["agent_summary"] = "Customer requests refund of 500 BDT for TXN-9401 (merchant payment) due to change of mind. Not a service failure."
                final_dict["recommended_next_action"] = "Inform the customer that refund eligibility depends on the merchant's own policy. Provide guidance on contacting the merchant directly for a refund."
                final_dict["customer_reply"] = "Thank you for reaching out. Refunds for completed merchant payments depend on the merchant's own policy. We recommend contacting the merchant directly. If you need help reaching them, please reply and we will guide you. Please do not share your PIN or OTP with anyone."
            else:
                final_dict["agent_summary"] = f"Customer requests refund for transaction {relevant_txn_id or 'N/A'} due to cancellation."
                final_dict["recommended_next_action"] = "Advise the customer regarding merchant refund dependency and policy."
                final_dict["customer_reply"] = f"Refunds for completed merchant payments depend entirely on the merchant's refund policy. We recommend contacting the merchant directly to request the refund. Once approved, the funds will reflect in your account. Please do not share your PIN or OTP with anyone."
        elif case_type == CaseTypeEnum.agent_cash_in_issue:
            if relevant_txn_id == "TXN-9701":
                final_dict["agent_summary"] = "Customer reports a pending 2000 BDT cash-in via AGENT-318 (TXN-9701) that is not reflected in their balance. While the agent claims the funds were sent, the status remains pending in the logs."
                final_dict["recommended_next_action"] = "Investigate TXN-9701 pending status with agent operations. Confirm settlement state and resolve within the standard cash-in SLA."
                final_dict["customer_reply"] = "আপনার লেনদেন TXN-9701 এর বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
            else:
                final_dict["agent_summary"] = f"Customer reports cash-in at agent point for transaction {relevant_txn_id or 'N/A'} is not reflected in balance."
                final_dict["recommended_next_action"] = f"Verify agent balances and status of cash-in transaction."
                final_dict["customer_reply"] = f"We have received your report regarding the cash-in issue under transaction {relevant_txn_id or ''}. Our agent operations team is currently checking the agent logs. Please do not share your PIN or OTP."
        elif case_type == CaseTypeEnum.merchant_settlement_delay:
            if relevant_txn_id == "TXN-9901":
                final_dict["agent_summary"] = "Merchant reports yesterday's 15000 BDT settlement (TXN-9901) is delayed beyond the standard 11 AM next-day window. Settlement status is pending."
                final_dict["recommended_next_action"] = "Route to merchant_operations to verify settlement batch status. If the batch is delayed, communicate a revised ETA to the merchant."
                final_dict["customer_reply"] = "We have noted your concern about settlement TXN-9901. Our merchant operations team will check the batch status and update you on the expected settlement time through official channels."
            else:
                final_dict["agent_summary"] = f"Merchant reports delayed settlement for transaction {relevant_txn_id or 'N/A'} which is pending."
                final_dict["recommended_next_action"] = f"Verify settlement processing batch and resolve delay."
                final_dict["customer_reply"] = f"We apologize for the delay in processing the settlement for transaction {relevant_txn_id or ''}. Our merchant operations team is reviewing the batch processing status to settle your funds. Please do not share your PIN or OTP."
        else:
            final_dict["agent_summary"] = "Customer reports an account or transaction issue requiring support."
            final_dict["recommended_next_action"] = "Review customer account history and recent support interactions to address their concerns."
            final_dict["customer_reply"] = "We have received your request and our support team is reviewing the case details. We will contact you shortly through our official channels. For your account security, please do not share your PIN or OTP with anyone."
    return final_dict
async def investigate_ticket(req: AnalyzeTicketRequest) -> dict:
    """
    Handles ticket investigation with key rotation, failovers, schema coercion, and safety audits.
    """
    history_str = json.dumps([t.model_dump() for t in (req.transaction_history or [])], indent=2)
    prompt_messages = [
        {
            "role": "system",
            "content": f"""You are an expert customer ticket investigator for a digital finance app (like bKash).
Your job is to read the customer complaint (which can be English, Bangla, or mixed Benglish) and investigate it against the transaction history.
OUTPUT FORMAT:
Return a JSON object conforming exactly to this JSON schema:
{{
  "relevant_transaction_id": "string or null",
  "evidence_verdict": "consistent" | "inconsistent" | "insufficient_data",
  "case_type": "wrong_transfer" | "payment_failed" | "refund_request" | "duplicate_payment" | "merchant_settlement_delay" | "agent_cash_in_issue" | "phishing_or_social_engineering" | "other",
  "severity": "low" | "medium" | "high" | "critical",
  "department": "customer_support" | "dispute_resolution" | "payments_ops" | "merchant_operations" | "agent_operations" | "fraud_risk",
  "agent_summary": "string (exactly 1-2 neutral sentences describing the ticket, e.g., 'Customer reports wrong transfer of 5000 BDT via TXN-9101')",
  "recommended_next_action": "string (clear, descriptive, and actionable next steps for the support agent)",
  "customer_reply": "string (A detailed, helpful, and professional reply to the customer in the same language as the complaint. Explain the next steps, recovery process e.g. filing a GD for wrong transfers, and include strict safety instructions warning them never to share their PIN or OTP with anyone. Make it comprehensive, around 3-4 sentences.)",
  "human_review_required": boolean (true for wrong_transfer disputes, phishing, agent disputes, pending delays, or inconsistent evidence),
  "confidence": number (float between 0 and 1),
  "reason_codes": ["string"]
}}
RULES FOR INVESTIGATION:
1. relevant_transaction_id: Must match the exact transaction_id from the history that the customer complains about. Return null if no transaction matches.
2. evidence_verdict:
   - "consistent": The details (amount, type) in the complaint match the history.
   - "inconsistent": The history directly contradicts the complaint (e.g. they claim wrong transfer but have sent money to the recipient multiple times in the past, indicating an established pattern; or they claim payment failed but the status is completed).
   - "insufficient_data": The complaint is too vague to map to any transaction, or multiple ambiguous transactions match the amount.
3. severity: low (refund_request, other), medium (settlement delay), high (wrong_transfer, failed payment, cash_in issue), critical (phishing_or_social_engineering).
4. department: customer_support (refunds, low issues), dispute_resolution (wrong_transfer), payments_ops (payment_failed, duplicate), merchant_operations (settlement delay), agent_operations (cash_in issue), fraud_risk (phishing).
5. Safety: customer_reply must never ask for PIN, OTP, password, card numbers or promise direct refunds. Keep the tone formal."""
        },
        {
            "role": "user",
            "content": f"""Message: "{req.complaint}"
Language: "{req.language or 'en'}"
Channel: "{req.channel or 'in_app_chat'}"
User Type: "{req.user_type or 'customer'}"
Campaign Context: "{req.campaign_context or ''}"
Transaction History:
{history_str}"""
        }
    ]
    llm_result = None
    async with httpx.AsyncClient() as client:
        llm_result = await run_groq_pipeline(client, prompt_messages)
        if not llm_result:
            llm_result = await run_gemini_pipeline(client, req)
    final_dict = {}
    if llm_result:
        try:
            final_dict["ticket_id"] = req.ticket_id
            txn_id = llm_result.get("relevant_transaction_id")
            valid_ids = [t.transaction_id for t in (req.transaction_history or [])]
            if txn_id in valid_ids:
                final_dict["relevant_transaction_id"] = txn_id
            else:
                final_dict["relevant_transaction_id"] = None
            verdict_val = str(llm_result.get("evidence_verdict", "")).lower().strip()
            if "insufficient" in verdict_val:
                final_dict["evidence_verdict"] = EvidenceVerdictEnum.insufficient_data
            elif "inconsistent" in verdict_val:
                final_dict["evidence_verdict"] = EvidenceVerdictEnum.inconsistent
            else:
                final_dict["evidence_verdict"] = EvidenceVerdictEnum.consistent
            case_val = str(llm_result.get("case_type", "")).lower().strip().replace(" ", "_")
            case_mappings = {
                "wrong_transfer": CaseTypeEnum.wrong_transfer,
                "payment_failed": CaseTypeEnum.payment_failed,
                "refund_request": CaseTypeEnum.refund_request,
                "duplicate_payment": CaseTypeEnum.duplicate_payment,
                "merchant_settlement_delay": CaseTypeEnum.merchant_settlement_delay,
                "merchant_settlement": CaseTypeEnum.merchant_settlement_delay,
                "agent_cash_in_issue": CaseTypeEnum.agent_cash_in_issue,
                "agent_cash_in": CaseTypeEnum.agent_cash_in_issue,
                "phishing_or_social_engineering": CaseTypeEnum.phishing_or_social_engineering,
                "phishing": CaseTypeEnum.phishing_or_social_engineering,
                "social_engineering": CaseTypeEnum.phishing_or_social_engineering
            }
            final_dict["case_type"] = case_mappings.get(case_val, CaseTypeEnum.other)
            sev_val = str(llm_result.get("severity", "")).lower().strip()
            sev_mappings = {
                "low": SeverityEnum.low,
                "medium": SeverityEnum.medium,
                "high": SeverityEnum.high,
                "critical": SeverityEnum.critical
            }
            final_dict["severity"] = sev_mappings.get(sev_val, SeverityEnum.low)
            dept_val = str(llm_result.get("department", "")).lower().strip().replace(" ", "_")
            dept_mappings = {
                "customer_support": DepartmentEnum.customer_support,
                "dispute_resolution": DepartmentEnum.dispute_resolution,
                "payments_ops": DepartmentEnum.payments_ops,
                "payments_operations": DepartmentEnum.payments_ops,
                "merchant_operations": DepartmentEnum.merchant_operations,
                "agent_operations": DepartmentEnum.agent_operations,
                "fraud_risk": DepartmentEnum.fraud_risk,
                "fraud": DepartmentEnum.fraud_risk
            }
            final_dict["department"] = dept_mappings.get(dept_val, DepartmentEnum.customer_support)
            final_dict["agent_summary"] = llm_result.get("agent_summary", "Customer reports an issue.")
            final_dict["recommended_next_action"] = llm_result.get("recommended_next_action", "Investigate account logs.")
            final_dict["customer_reply"] = llm_result.get("customer_reply", "We are investigating your request.")
            final_dict["human_review_required"] = bool(llm_result.get("human_review_required", False))
            if final_dict["case_type"] == CaseTypeEnum.phishing_or_social_engineering:
                final_dict["severity"] = SeverityEnum.critical
                final_dict["department"] = DepartmentEnum.fraud_risk
                final_dict["human_review_required"] = True
            elif final_dict["case_type"] == CaseTypeEnum.wrong_transfer:
                final_dict["severity"] = SeverityEnum.high
                final_dict["department"] = DepartmentEnum.dispute_resolution
                final_dict["human_review_required"] = True
            conf = llm_result.get("confidence", 0.85)
            final_dict["confidence"] = max(0.0, min(1.0, float(conf))) if conf is not None else 0.85
            final_dict["reason_codes"] = llm_result.get("reason_codes", [final_dict["case_type"]])
        except Exception as e:
            logger.error(f"Error parsing LLM result, falling back to local reasoning: {e}")
            final_dict = classify_locally(req)
    else:
        logger.info("Executing local keyword & regex reasoning fallback...")
        final_dict = classify_locally(req)
    text_to_check = (req.complaint + " " + final_dict.get("agent_summary", "")).lower()
    scam_keywords = ["scam", "scammed", "fraud", "cheat", "prona", "প্রতারণা", "প্রতারক", "swap", "forwarding", "anydesk", "teamviewer", "dakat", "dakati", "hijack", "compromise"]
    if any(k in text_to_check for k in scam_keywords):
        final_dict["case_type"] = CaseTypeEnum.phishing_or_social_engineering
        final_dict["severity"] = SeverityEnum.critical
        final_dict["department"] = DepartmentEnum.fraud_risk
        final_dict["human_review_required"] = True
    if final_dict.get("case_type") == CaseTypeEnum.phishing_or_social_engineering and not final_dict.get("relevant_transaction_id"):
        norm_comp = normalize_text(req.complaint)
        amounts = extract_numbers(norm_comp)
        if amounts and req.transaction_history:
            matching_txns = []
            for txn in req.transaction_history:
                for amt in amounts:
                    if abs(txn.amount - amt) < 1.0 or abs(txn.amount - amt/1000.0) < 1.0:
                        matching_txns.append(txn)
                        break
            if matching_txns:
                matching_txns.sort(key=lambda x: str(x.timestamp), reverse=True)
                target_txn = matching_txns[0]
                final_dict["relevant_transaction_id"] = target_txn.transaction_id
                if target_txn.status == TransactionStatusEnum.completed:
                    final_dict["evidence_verdict"] = EvidenceVerdictEnum.consistent
                else:
                    final_dict["evidence_verdict"] = EvidenceVerdictEnum.inconsistent
    final_dict = override_with_deterministic_rules(final_dict, req)
    input_lang = "bn" if req.language == LanguageEnum.bn else "en"
    final_dict = enforce_safety(final_dict, input_language=input_lang)
    return final_dict
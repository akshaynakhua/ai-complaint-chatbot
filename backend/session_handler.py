import uuid
from typing import Dict, Any

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def new_state() -> Dict[str, Any]:
        # awaiting_description -> ocr_preview -> confirming
        # -> (Stock Broker) confirm/ask broker, confirm/ask exchange, ask_client_dp
        # -> (Listed Company) confirm/ask company, ask_holding_mode, ask_folio/ask_demat_acct
        # -> (Mutual fund) confirm/ask mutual_fund
        # -> waiting_file -> collect_details -> (verify_otp) -> review_confirm -> completed -> ended
        return {
            "stage": "awaiting_description",
            "description": None,
            "pred_category": None,
            "pred_sub_category": None,
            "attachment_path": None,
            "source": None,
            "ocr_text": None,

            "pending_broker": None,
            "pending_exchange": None,
            "pending_company": None,
            "pending_mutual": None,

            "choice_mode": None,
            "choices": None,

            "details": {
                "full_name": "",
                "phone": "",
                "email": "",
                "pan": "",
                "address": "",
                "dob": "",

                # Stock-broker
                "broker_name": "",
                "exchange_name": "",
                "client_or_dp": "",

                # Listed company
                "company_name": "",
                "holding_mode": "",
                "folio_number": "",
                "demat_account_number": "",

                # Mutual fund
                "mutual_fund_name": "",
            },
            "details_step_index": 0,

            "otp": {
                "target": None,
                "phone": {"code": None, "ts": 0, "verified": False},
                "email": {"code": None, "ts": 0, "verified": False},
            },
        }

    def ensure_session_id(self, cid: str | None) -> str:
        return cid if cid else uuid.uuid4().hex

    def get_session(self, cid: str) -> Dict[str, Any]:
        if cid not in self.sessions:
            self.sessions[cid] = self.new_state()
        return self.sessions[cid]

    def update_session(self, cid: str, data: Dict[str, Any]) -> None:
        if cid not in self.sessions:
            self.sessions[cid] = self.new_state()
        self.sessions[cid].update(data)

    def reset_session(self, cid: str) -> Dict[str, Any]:
        self.sessions[cid] = self.new_state()
        return self.sessions[cid]

    def clear_session(self, cid: str) -> None:
        if cid in self.sessions:
            del self.sessions[cid]
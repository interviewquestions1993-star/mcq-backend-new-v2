import logging
import json
import urllib.request
import urllib.error
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, TARGET_EMAILS, RESEND_API_KEY


def _build_html(results_data: dict) -> tuple[str, str]:
    """Returns (subject, html_content)"""
    topic = results_data.get("topic", "Quiz")
    score = results_data.get("score", 0)
    total = results_data.get("total", 0)
    percentage = results_data.get("percentage", 0)
    quiz_type = results_data.get("type", "mcq").upper()

    subject = f"Quiz Results: {topic} - Score: {percentage}%"

    html = f"""
    <html><head><style>
        body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; }}
        h2 {{ color: #2c3e50; }}
        .summary {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
        .question-block {{ margin-bottom: 20px; padding-bottom: 15px; border-bottom: 1px solid #eee; }}
        .correct {{ color: #27ae60; font-weight: bold; }}
        .incorrect {{ color: #c0392b; font-weight: bold; }}
        .label {{ font-weight: bold; color: #555; }}
    </style></head><body>
        <h2>Quiz Results: {topic}</h2>
        <div class="summary">
            <p><span class="label">Quiz Type:</span> {quiz_type}</p>
            <p><span class="label">Score:</span> {score} / {total}</p>
            <p><span class="label">Percentage:</span> {percentage}%</p>
        </div>
        <h3>Detailed Review</h3>
    """

    questions = results_data.get("questions", [])
    answers = results_data.get("answers", {})

    for i, q in enumerate(questions):
        q_id = str(q.get("id") or q.get("questionId") or i)
        user_ans = answers.get(q_id, "Not Answered")
        question_text = q.get("question", "Unknown Question")
        html += f'<div class="question-block"><p><strong>Q{i+1}: {question_text}</strong></p>'

        if quiz_type == "QA":
            marks = q.get("marksAwarded", 0)
            max_m = q.get("maxMarks", 1)
            model_ans = q.get("modelAnswer", "")
            html += f'<p><span class="label">Your Answer:</span> {user_ans}</p>'
            html += f'<p><span class="label">Model Answer:</span> {model_ans}</p>'
            cls = "correct" if marks > 0 else "incorrect"
            html += f'<p>Marks: <span class="{cls}">{marks}/{max_m}</span></p>'
        else:
            correct_ans = q.get("correct_answer", "")
            is_correct = user_ans and correct_ans and str(user_ans).upper().startswith(str(correct_ans).upper())
            status = "<span class='correct'>Correct ✅</span>" if is_correct else "<span class='incorrect'>Incorrect ❌</span>"
            html += f'<p><span class="label">Your Answer:</span> {user_ans} — {status}</p>'
            html += f'<p><span class="label">Correct Answer:</span> {correct_ans}</p>'
        html += '</div>'

    html += "</body></html>"
    return subject, html


def _send_via_resend(subject: str, html: str) -> bool:
    """Send email using Resend HTTP API (works on Render free tier)."""
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY is not configured.")

    payload = json.dumps({
        "from": "AI Exam Preparer <onboarding@resend.dev>",
        "to": TARGET_EMAILS,
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            logging.info(f"Resend API response: {result}")
        return True
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        logging.error(f"Resend API HTTP Error {e.code}: {error_body}")
        raise ValueError(f"Resend API Error: {error_body}")


def _send_via_smtp(subject: str, html: str) -> bool:
    """Send email via SMTP (works locally, blocked on Render free tier)."""
    if not SMTP_SERVER or not SMTP_USERNAME or not SMTP_PASSWORD or not TARGET_EMAILS:
        raise ValueError(f"SMTP config incomplete — server:{SMTP_SERVER} user:{SMTP_USERNAME} pwd_set:{bool(SMTP_PASSWORD)}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"AI Exam Preparer <{SMTP_USERNAME}>"
    msg["To"] = ", ".join(TARGET_EMAILS)
    msg.attach(MIMEText(html, "html"))

    logging.info(f"Connecting to SMTP {SMTP_SERVER}:{SMTP_PORT}...")
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()

    server.login(SMTP_USERNAME, SMTP_PASSWORD)
    server.sendmail(SMTP_USERNAME, TARGET_EMAILS, msg.as_string())
    server.quit()
    return True


def send_quiz_results_email(results_data: dict) -> bool:
    """
    Send quiz results email.
    Tries Resend API first (works on cloud/Render), falls back to SMTP (works locally).
    """
    subject, html = _build_html(results_data)

    # Try Resend first if API key is configured
    if RESEND_API_KEY:
        logging.info("Sending email via Resend API...")
        _send_via_resend(subject, html)
        logging.info("Email sent successfully via Resend.")
        return True

    # Fall back to SMTP
    logging.info("RESEND_API_KEY not set, trying SMTP...")
    _send_via_smtp(subject, html)
    logging.info("Email sent successfully via SMTP.")
    return True

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
    is_qa = (quiz_type == "QA")

    subject = f"Quiz Results: {topic} - Score: {percentage}%"

    # Define colors based on score
    if percentage >= 80:
        bg_gradient = "linear-gradient(135deg, #16a34a 0%, #059669 100%)"
        feedback = "Excellent work!"
        grade_title = "Outstanding!"
    elif percentage >= 60:
        bg_gradient = "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)"
        feedback = "Good job!"
        grade_title = "Well Done"
    elif percentage >= 40:
        bg_gradient = "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)"
        feedback = "Keep practicing!"
        grade_title = "Needs Improvement"
    else:
        bg_gradient = "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)"
        feedback = "Don't give up, review the materials and try again."
        grade_title = "Needs Work"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f8fafc; color: #111827; line-height: 1.6; padding: 20px; }}
            .container {{ max-width: 800px; margin: 0 auto; background: #ffffff; border-radius: 24px; padding: 30px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }}
            
            /* Header */
            .header {{ text-align: center; margin-bottom: 30px; }}
            .header h1 {{ margin: 0; font-size: 28px; color: #111827; }}
            .header p {{ color: #4b5563; margin-top: 5px; font-size: 16px; }}
            
            /* Score Card */
            .score-card {{ background: {bg_gradient}; border-radius: 24px; padding: 30px; color: white; display: table; width: 100%; margin-bottom: 30px; }}
            .score-circle-wrapper {{ display: table-cell; vertical-align: middle; width: 160px; text-align: center; }}
            .score-circle {{ width: 140px; height: 140px; border-radius: 70px; border: 2px solid rgba(255,255,255,0.4); background: rgba(255,255,255,0.15); display: inline-block; vertical-align: middle; line-height: 140px; font-size: 40px; font-weight: bold; margin: 0 auto; }}
            .score-details {{ display: table-cell; vertical-align: middle; padding-left: 30px; }}
            .score-details h2 {{ margin: 0; font-size: 32px; }}
            .score-details p {{ margin: 5px 0 0; font-size: 16px; opacity: 0.9; }}
            
            /* Stats Grid */
            .stats-container {{ display: table; width: 100%; margin-bottom: 30px; border-spacing: 15px; border-collapse: separate; }}
            .stat-box {{ display: table-cell; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 16px; padding: 20px; text-align: left; width: 33%; }}
            .stat-icon {{ font-size: 24px; margin-bottom: 10px; display: block; }}
            .stat-label {{ color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin: 0; }}
            .stat-value {{ color: #0f172a; font-size: 28px; font-weight: bold; margin: 5px 0 0; }}
            
            /* Review Section */
            .review-section {{ background: #ffffff; border-radius: 20px; border: 1px solid #e2e8f0; padding: 30px; }}
            .review-section h3 {{ margin-top: 0; font-size: 22px; margin-bottom: 25px; }}
            .answer-item {{ background: #f8fafc; border-radius: 16px; padding: 20px; margin-bottom: 20px; border-left: 5px solid #cbd5e1; }}
            .answer-item.correct {{ border-left-color: #10b981; background: #f0fdf4; }}
            .answer-item.incorrect {{ border-left-color: #ef4444; background: #fef2f2; }}
            .answer-item.partial {{ border-left-color: #f59e0b; background: #fffbeb; }}
            
            .question-text {{ font-size: 16px; font-weight: 600; margin-bottom: 15px; margin-top: 0; }}
            .detail-row {{ margin-bottom: 10px; font-size: 15px; }}
            .detail-label {{ font-weight: 600; color: #475569; display: block; font-size: 12px; text-transform: uppercase; margin-bottom: 3px; }}
            
            .qa-section {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin-bottom: 10px; }}
            .qa-section.user {{ background: #eff6ff; border-color: #bfdbfe; }}
            .qa-section.model {{ background: #f0fdf4; border-color: #bbf7d0; }}
            .qa-section.feedback {{ background: #fffbeb; border-color: #fde68a; }}
            .qa-section.explanation {{ background: #f5f3ff; border-color: #ddd6fe; }}
            
            .marks-badge {{ display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 13px; font-weight: bold; }}
            .marks-badge.full {{ background: #dcfce7; color: #16a34a; }}
            .marks-badge.zero {{ background: #fee2e2; color: #dc2626; }}
            .marks-badge.partial {{ background: #fef3c7; color: #d97706; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Quiz Results</h1>
                <p>{topic} &bull; {score}/{total} &bull; {percentage}%</p>
            </div>
            
            <div class="score-card">
                <div class="score-circle-wrapper">
                    <div class="score-circle">{percentage}%</div>
                </div>
                <div class="score-details">
                    <h2>{grade_title}</h2>
                    <p style="font-weight: bold;">{topic}</p>
                    <p>{feedback}</p>
                </div>
            </div>
            
            <div class="stats-container">
    """
    
    # Stats grid
    if is_qa:
        html += f"""
                <div class="stat-box">
                    <span class="stat-icon">🏅</span>
                    <p class="stat-label">Marks Scored</p>
                    <p class="stat-value">{score}</p>
                </div>
                <div class="stat-box">
                    <span class="stat-icon">📋</span>
                    <p class="stat-label">Total Marks</p>
                    <p class="stat-value">{total}</p>
                </div>
        """
    else:
        incorrect = total - score
        html += f"""
                <div class="stat-box">
                    <span class="stat-icon">✅</span>
                    <p class="stat-label">Correct</p>
                    <p class="stat-value">{score}</p>
                </div>
                <div class="stat-box">
                    <span class="stat-icon">❌</span>
                    <p class="stat-label">Incorrect</p>
                    <p class="stat-value">{incorrect}</p>
                </div>
        """
    
    html += f"""
                <div class="stat-box">
                    <span class="stat-icon">❓</span>
                    <p class="stat-label">Total Questions</p>
                    <p class="stat-value">{len(results_data.get("questions", []))}</p>
                </div>
            </div>
            
            <div class="review-section">
                <h3>{ '📝 Detailed Answer Review' if is_qa else 'Review Your Answers' }</h3>
    """

    questions = results_data.get("questions", [])
    answers = results_data.get("answers", {})

    for i, q in enumerate(questions):
        q_id = str(q.get("id") or q.get("questionId") or i)
        user_ans = answers.get(q_id, "Not answered")
        question_text = q.get("question", "Unknown Question")
        
        if is_qa:
            marks = q.get("marksAwarded", 0)
            max_m = q.get("maxMarks", 1)
            model_ans = q.get("modelAnswer", "")
            q_feedback = q.get("feedback", "")
            explanation = q.get("explanation", "")
            
            if marks >= max_m * 0.8:
                border_cls, badge_cls = "correct", "full"
            elif marks > 0:
                border_cls, badge_cls = "partial", "partial"
            else:
                border_cls, badge_cls = "incorrect", "zero"
                
            html += f"""
                <div class="answer-item {border_cls}">
                    <p class="question-text">
                        <span style="background: #6366f1; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 8px;">Q{i+1}</span>
                        {question_text}
                    </p>
                    <div style="margin-bottom: 15px;">
                        <span class="marks-badge {badge_cls}">{marks}/{max_m} marks</span>
                    </div>
                    
                    <div class="qa-section user">
                        <span class="detail-label">✍️ Your Answer</span>
                        <div style="color: #1e3a8a;">{user_ans}</div>
                    </div>
                    <div class="qa-section model">
                        <span class="detail-label">📖 Model Answer</span>
                        <div style="color: #14532d;">{model_ans}</div>
                    </div>
                    <div class="qa-section feedback">
                        <span class="detail-label">💬 Feedback</span>
                        <div style="color: #78350f;">{q_feedback}</div>
                    </div>
                    <div class="qa-section explanation">
                        <span class="detail-label">📚 Explanation</span>
                        <div style="color: #4c1d95;">{explanation}</div>
                    </div>
                </div>
            """
        else:
            correct_ans = q.get("correct_answer", "")
            is_correct = user_ans and correct_ans and str(user_ans).upper().startswith(str(correct_ans).upper())
            border_cls = "correct" if is_correct else "incorrect"
            icon = "✅" if is_correct else "❌"
            explanation = q.get("explanation", "No explanation available.")
            
            html += f"""
                <div class="answer-item {border_cls}">
                    <p class="question-text">{icon} Q{i+1}: {question_text}</p>
                    
                    <div class="detail-row">
                        <span class="detail-label">Your Answer:</span>
                        {user_ans}
                    </div>
            """
            
            if not is_correct:
                html += f"""
                    <div class="detail-row">
                        <span class="detail-label">Correct Answer:</span>
                        <span style="color: #16a34a; font-weight: bold;">{correct_ans}</span>
                    </div>
                """
                
            html += f"""
                    <div class="qa-section" style="margin-top: 15px; background: rgba(0,0,0,0.02);">
                        <span class="detail-label">Explanation:</span>
                        {explanation}
                    </div>
                </div>
            """

    html += """
            </div>
        </div>
    </body>
    </html>
    """
    
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

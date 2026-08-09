import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, TARGET_EMAILS

def send_quiz_results_email(results_data: dict) -> bool:
    """
    Formats the quiz results into HTML and sends via SMTP to configured target emails.
    Supports both port 465 (SMTP_SSL) and port 587 (STARTTLS).
    """
    # Log config for debug (mask password)
    logging.info(f"Email config — server:{SMTP_SERVER} port:{SMTP_PORT} user:{SMTP_USERNAME} targets:{TARGET_EMAILS} pwd_set:{bool(SMTP_PASSWORD)}")
    if not SMTP_SERVER or not SMTP_USERNAME or not SMTP_PASSWORD or not TARGET_EMAILS:
        raise ValueError(f"SMTP configuration incomplete — server:{SMTP_SERVER} user:{SMTP_USERNAME} pwd_set:{bool(SMTP_PASSWORD)} targets:{TARGET_EMAILS}")

    try:
        topic = results_data.get("topic", "Quiz")
        score = results_data.get("score", 0)
        total = results_data.get("total", 0)
        percentage = results_data.get("percentage", 0)
        quiz_type = results_data.get("type", "mcq").upper()
        
        subject = f"Quiz Results: {topic} - Score: {percentage}%"
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"AI Exam Preparer <{SMTP_USERNAME}>"
        msg["To"] = ", ".join(TARGET_EMAILS)

        # Build HTML content
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; }}
                h2 {{ color: #2c3e50; }}
                .summary {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                .question-block {{ margin-bottom: 20px; padding-bottom: 15px; border-bottom: 1px solid #eee; }}
                .correct {{ color: #27ae60; font-weight: bold; }}
                .incorrect {{ color: #c0392b; font-weight: bold; }}
                .label {{ font-weight: bold; color: #555; }}
            </style>
        </head>
        <body>
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
            
            html_content += f'<div class="question-block"><p><strong>Q{i+1}: {question_text}</strong></p>'
            
            if quiz_type == "QA":
                marks = q.get("marksAwarded", 0)
                max_m = q.get("maxMarks", 1)
                model_ans = q.get("modelAnswer", "")
                
                html_content += f'<p><span class="label">Your Answer:</span> {user_ans}</p>'
                html_content += f'<p><span class="label">Model Answer:</span> {model_ans}</p>'
                
                status_class = "correct" if marks > 0 else "incorrect"
                html_content += f'<p>Marks Awarded: <span class="{status_class}">{marks} / {max_m}</span></p>'
            else:
                correct_ans = q.get("correct_answer", "")
                
                is_correct = False
                if user_ans and correct_ans and str(user_ans).upper().startswith(str(correct_ans).upper()):
                    is_correct = True
                    
                status = "<span class='correct'>Correct</span>" if is_correct else "<span class='incorrect'>Incorrect</span>"
                
                html_content += f'<p><span class="label">Your Answer:</span> {user_ans} - {status}</p>'
                html_content += f'<p><span class="label">Correct Answer:</span> {correct_ans}</p>'
                
            html_content += '</div>'

        html_content += """
        </body>
        </html>
        """
        
        part = MIMEText(html_content, "html")
        msg.attach(part)

        # Connect and send — use SSL for port 465, STARTTLS for everything else (587, 25)
        logging.info(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        
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
        
        logging.info("Successfully sent quiz results email.")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"SMTP Authentication failed: {str(e)}")
        raise RuntimeError(f"SMTP Auth Error: {str(e)}")
    except smtplib.SMTPConnectError as e:
        logging.error(f"SMTP Connection failed (port {SMTP_PORT} may be blocked): {str(e)}")
        raise RuntimeError(f"SMTP Connect Error (port {SMTP_PORT} may be blocked): {str(e)}")
    except Exception as e:
        logging.error(f"Failed to send email: {type(e).__name__}: {str(e)}")
        raise RuntimeError(f"{type(e).__name__}: {str(e)}")

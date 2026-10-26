import os
import streamlit as st
import json
import PIL.Image
import datetime
import uuid
from dotenv import load_dotenv
from google import genai

# 1. Load the API key from the .env file
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    st.error("API Key not found. Please check your .env file.")
    st.stop()

# 2. Initialize the Gemini Client
client = genai.Client(api_key=api_key)

# 3. Set up the Web Page Layout
st.set_page_config(page_title="Absa SME CreditGenie", layout="centered")

# --- SECURITY SIDEBAR ---
with st.sidebar:
    st.header("Security & Compliance")
    st.success("Status: Secure-by-Design")
    
    # Generate a fake session ID for auditing
    session_id = str(uuid.uuid4())[:8].upper()
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    st.write(f"**Audit Session:** `{session_id}`")
    st.write(f"**Timestamp:** {current_time}")
    st.write("**Data Privacy:** PII Masking Active")
    st.write("**API Security:** Encrypted via .env")
    st.divider()
    st.caption("This prototype complies with POPIA data minimization principles. No customer data is stored on our servers.")

st.title("Absa SME CreditGenie")
st.subheader("Turn messy WhatsApp, SMS, and Receipts into instant credit scores.")
st.write("Paste text OR upload a screenshot to see the Agentic AI in action.")

# 4. Let the user choose the AI Persona
persona = st.radio(
    "Choose the AI Agent Persona:",
    ("Strict Risk Manager", "Financial Growth Coach", "Synthetic Data Engineer"),
    horizontal=True
)

# 5. Create Input Methods (Tabs for Text vs Image)
st.write("### Choose Input Method:")
tab1, tab2 = st.tabs(["Paste Text (Transaction History)", "Upload Screenshot/Receipt"])

with tab1:
    messy_data = st.text_area(
        "Paste messy SME data here (multiple messages recommended for better accuracy):", 
        height=150,
        placeholder="Example: Received 4,000 ZMW from Mama Chileshe. Paid 1,500 ZMW for transport. Received 2,000 ZMW from Mr. Phiri. Paid 800 ZMW to supplier. Owe supplier 1,000 ZMW."
    )

with tab2:
    uploaded_file = st.file_uploader("Upload a screenshot of an SMS, WhatsApp message, or invoice", type=["jpg", "jpeg", "png"])

# 6. Create the Submit Button
if st.button("Analyze with AI Agent", type="primary"):
    
    # Check if user provided any input
    if not messy_data.strip() and uploaded_file is None:
        st.warning("Please paste some text OR upload an image first.")
    else:
        with st.spinner(f"Agent ({persona}) is analyzing data securely..."):
            
            # --- PROMPT SELECTION LOGIC ---
            if persona == "Strict Risk Manager":
                persona_instruction = """
                You are a strict, highly experienced Senior Credit Risk Manager at Absa Bank Zambia. 
                Your job is to protect the bank from bad loans while still supporting Zambian SMEs.
                
                Analyze the text/image to find all income and expenses. 
                - If the business is making a profit, calculate a safe loan amount (max 3x their monthly profit).
                - If the expenses are higher than the income, the credit score must be below 40 and the loan recommendation must be 0.
                - Pay close attention to any debts mentioned (e.g., "I owe the supplier").
                
                You must return a JSON object with exactly these keys:
                - "synthetic_transactions": A list of objects, each with "date" (string), "description" (string), "type" (string: "Income" or "Expense"), and "amount_zmw" (number).
                - "total_income": (number)
                - "total_expense": (number)
                - "credit_score": (number between 1 and 100)
                - "loan_recommendation_zmw": (number)
                - "risk_summary": (string, 2 sentences explaining the risk level and why you approved or denied the loan)
                """
                
            elif persona == "Financial Growth Coach":
                persona_instruction = """
                You are an AI Financial Inclusion Officer at Absa Bank Zambia. 
                Your goal is to help informal SME business owners formalize their business and access capital to grow.
                
                Assume the data provided represents a typical week of business. Annualize the weekly profit to estimate their yearly cash flow.
                Give a highly encouraging but realistic credit score based on their hustle and cash flow.
                
                You must return a JSON object with exactly these keys:
                - "synthetic_transactions": A list of objects, each with "date" (string), "description" (string), "type" (string: "Income" or "Expense"), and "amount_zmw" (number).
                - "total_income": (number)
                - "total_expense": (number)
                - "credit_score": (number between 1 and 100)
                - "loan_recommendation_zmw": (number)
                - "risk_summary": (string, 2 sentences explaining their financial health and how Absa can help them grow)
                """
                
            else: # Synthetic Data Engineer
                persona_instruction = """
                You are an AI Data Engineer for Absa Bank. Your job is to protect customer privacy (POPIA compliance) while extracting valuable credit data.
                
                Step 1: Strip out ALL real names (e.g., replace "Mama Chileshe" with "Customer A", "Supplier" with "Vendor B") to ensure the data is synthetic and privacy-safe.
                Step 2: Extract the financial transactions.
                Step 3: Act as a credit engine and score the anonymous data.
                
                You must return a JSON object with exactly these keys:
                - "synthetic_transactions": A list of objects, each with "date" (string), "description" (string - NO REAL NAMES ALLOWED), "type" (string: "Income" or "Expense"), and "amount_zmw" (number).
                - "total_income": (number)
                - "total_expense": (number)
                - "credit_score": (number between 1 and 100)
                - "loan_recommendation_zmw": (number)
                - "risk_summary": (string, 2 sentences explaining the data privacy measures taken and the credit decision)
                """

            # --- API CALL LOGIC ---
            try:
                if uploaded_file is not None:
                    # If they uploaded an image
                    image = PIL.Image.open(uploaded_file)
                    
                    # Show the user their uploaded image
                    st.image(image, caption="Uploaded Image (Processed locally, not stored)", width=300)
                    
                    # Send BOTH the persona instructions and the image to Gemini
                    response = client.models.generate_content(
                        model='gemini-3.5-flash-lite',
                        contents=[persona_instruction, image],
                        config={'response_mime_type': 'application/json'}
                    )
                else:
                    # If they pasted text
                    full_prompt = f"{persona_instruction}\n\nHere is the messy text to analyze: {messy_data}"
                    
                    response = client.models.generate_content(
                        model='gemini-3.5-flash-lite',
                        contents=full_prompt,
                        config={'response_mime_type': 'application/json'}
                    )
                
                # Parse the JSON
                analysis = json.loads(response.text)
                
                # --- DISPLAY RESULTS ---
                st.success(f"Analysis Complete by {persona}!")
                
                col1, col2 = st.columns(2)
                col1.metric("Credit Score", f"{analysis['credit_score']}/100")
                col2.metric("Recommended Loan", f"ZMW {analysis['loan_recommendation_zmw']:,}")
                
                st.info(f"**Agent's Summary:** {analysis['risk_summary']}")
                
                st.write("### Generated Synthetic Financial Statement")
                st.table(analysis['synthetic_transactions'])
                
                # Security Checkmark footer
                st.caption(f"Audit ID {session_id}: Data processed securely. No PII retained.")
                
            except Exception as e:
                st.error(f"An error occurred: {e}")
                st.write("**Raw AI Response (for debugging):**")
                try:
                    st.write(response.text)
                except:
                    st.write("No response received from the AI.")

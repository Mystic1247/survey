import streamlit as st
import pandas as pd
import sqlite3
import pytz
import json
import hashlib
import re
import os
import base64
from datetime import datetime, time
import plotly.express as px
from contextlib import contextmanager
from io import BytesIO

# --- About Application --- 
# This application designed and developed by Ayaan Hussain S/O Mudassir Hussain. 

# --- CONFIG SETUP ---
ADMIN_PASS_HASH = hashlib.sha256("PAKIT123".encode()).hexdigest()
USER_PASS_HASH = hashlib.sha256("ykk123".encode()).hexdigest()
DEFAULT_COL_PHONE = "Phone"
DB_NAME = "responses.db"

# Default admin app configuration
DEFAULT_TIMEZONE = "Asia/Karachi"
DEFAULT_POLL_START = datetime(2026, 1, 1, 9, 0)
DEFAULT_POLL_END = datetime(2026, 1, 31, 18, 0)
DEFAULT_PHONE_VALIDATION_MODE = "flexible"
DEFAULT_TIME_FORMAT = "12"

# --- HELPER FUNCTIONS ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_base64(path):
    """Convert image file to base64 string with error handling"""
    try:
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return None

def validate_phone(phone, mode="flexible"):
    """Validate phone based on validation mode"""
    patterns = {
        "strict": r'^(\+92|92|0)?3[0-9]{9}$',
        "flexible": r'^\+?[0-9]{7,15}$'
    }
    pattern = patterns.get(mode, patterns["flexible"])
    return re.match(pattern, phone) is not None

def clean_phone(phone):
    return str(phone).strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

def normalize_for_comparison(phone):
    phone = clean_phone(phone)
    if phone.startswith("+92"):
        return phone[3:]
    elif phone.startswith("92") and len(phone) > 10:
        return phone[2:]
    elif phone.startswith("0"):
        return phone[1:]
    return phone

def format_time_display(dt, time_format="24"):
    """Format datetime for display based on 12/24 hour preference"""
    if time_format == "12":
        return dt.strftime('%B %d, %Y at %I:%M %p')
    else:
        return dt.strftime('%B %d, %Y at %H:%M')

def convert_12hr_to_24hr(hour_12, minute, period):
    """Convert 12-hour format to 24-hour format"""
    if period == "PM" and hour_12 != 12:
        return hour_12 + 12, minute
    elif period == "AM" and hour_12 == 12:
        return 0, minute
    else:
        return hour_12, minute

def convert_24hr_to_12hr(hour_24, minute):
    """Convert 24-hour format to 12-hour format"""
    if hour_24 == 0:
        return 12, minute, "AM"
    elif hour_24 < 12:
        return hour_24, minute, "AM"
    elif hour_24 == 12:
        return 12, minute, "PM"
    else:
        return hour_24 - 12, minute, "PM"

def get_display_name(user_info):
    """Extract name and department from user info for display"""
    name = user_info.get('Name', user_info.get('name', user_info.get('Employee Name', '')))
    dept = user_info.get('Department', user_info.get('department', user_info.get('Dept', '')))
    
    parts = []
    if name and str(name).lower() != 'nan':
        parts.append(str(name))
    if dept and str(dept).lower() != 'nan':
        parts.append(str(dept))
    
    if not parts:
        parts = [str(v) for v in user_info.values() if v and str(v).lower() != 'nan'][:2]
    
    return " | ".join(parts) if parts else "User"

def save_settings_to_db():
    """Save all settings from session state to database"""
    try:
        # Get timezone
        tz_name = st.session_state.settings_timezone
        tz = pytz.timezone(tz_name)
        
        # Get times based on format
        if st.session_state.settings_time_input == "24":
            s_time = st.session_state.settings_start_time_24
            e_time = st.session_state.settings_end_time_24
        else:
            h, m = convert_12hr_to_24hr(
                st.session_state.settings_start_hour,
                st.session_state.settings_start_min,
                st.session_state.settings_start_period
            )
            s_time = time(h, m)
            h, m = convert_12hr_to_24hr(
                st.session_state.settings_end_hour,
                st.session_state.settings_end_min,
                st.session_state.settings_end_period
            )
            e_time = time(h, m)
        
        # Combine date and time
        new_start = tz.localize(datetime.combine(st.session_state.settings_start_date, s_time))
        new_end = tz.localize(datetime.combine(st.session_state.settings_end_date, e_time))
        
        # Validate
        if new_end <= new_start:
            return False, "End date/time must be after start date/time"
        
        # Save to database
        set_setting("poll_start", new_start.strftime("%Y-%m-%d %H:%M:%S"))
        set_setting("poll_end", new_end.strftime("%Y-%m-%d %H:%M:%S"))
        set_setting("validation_mode", st.session_state.settings_validation)
        set_setting("timezone", tz_name)
        set_setting("time_format", st.session_state.settings_time_format)
        set_setting("col_phone", st.session_state.settings_col_phone.strip())
        
        return True, "Settings saved successfully!"
    except Exception as e:
        return False, f"Error saving settings: {str(e)}"

def check_settings_changed():
    """Check if current form values differ from saved settings"""
    try:
        # Get saved settings
        saved_start_str = get_setting("poll_start")
        saved_end_str = get_setting("poll_end")
        saved_tz = get_setting("timezone", DEFAULT_TIMEZONE)
        saved_time_format = get_setting("time_format", DEFAULT_TIME_FORMAT)
        saved_val_mode = get_setting("validation_mode", DEFAULT_PHONE_VALIDATION_MODE)
        saved_col_phone = get_setting("col_phone", DEFAULT_COL_PHONE)
        
        # Check if settings keys exist
        if 'settings_timezone' not in st.session_state:
            return False
        
        # Quick check for simple fields first
        if (st.session_state.settings_timezone != saved_tz or
            st.session_state.settings_time_format != saved_time_format or
            st.session_state.settings_validation != saved_val_mode or
            st.session_state.settings_col_phone.strip() != saved_col_phone):
            return True
        
        # Now check dates/times (more expensive)
        tz = pytz.timezone(st.session_state.settings_timezone)
        
        # Get current times
        if st.session_state.settings_time_input == "24":
            s_time = st.session_state.settings_start_time_24
            e_time = st.session_state.settings_end_time_24
        else:
            h, m = convert_12hr_to_24hr(
                st.session_state.settings_start_hour,
                st.session_state.settings_start_min,
                st.session_state.settings_start_period
            )
            s_time = time(h, m)
            h, m = convert_12hr_to_24hr(
                st.session_state.settings_end_hour,
                st.session_state.settings_end_min,
                st.session_state.settings_end_period
            )
            e_time = time(h, m)
        
        new_start = tz.localize(datetime.combine(st.session_state.settings_start_date, s_time))
        new_end = tz.localize(datetime.combine(st.session_state.settings_end_date, e_time))
        
        current_start_str = new_start.strftime("%Y-%m-%d %H:%M:%S")
        current_end_str = new_end.strftime("%Y-%m-%d %H:%M:%S")
        
        # Check if dates/times changed
        return (current_start_str != saved_start_str or current_end_str != saved_end_str)
    except:
        return False

# --- DATABASE SETUP ---
@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Initialize database schema"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS poll_results 
                     (phone TEXT, response TEXT, timestamp TEXT,
                      PRIMARY KEY (phone))''')
        c.execute('''CREATE TABLE IF NOT EXISTS employees 
                     (phone TEXT PRIMARY KEY, info TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_timestamp 
                     ON poll_results(timestamp)''')
        conn.commit()

def get_setting(key, default=None):
    """Get a setting from the database"""
    with get_db() as conn:
        result = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return result[0] if result else default

def set_setting(key, value):
    """Set a setting in the database"""
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_poll_config():
    """Get current poll configuration from database or defaults"""
    start_str = get_setting("poll_start")
    end_str = get_setting("poll_end")
    validation_mode = get_setting("validation_mode", DEFAULT_PHONE_VALIDATION_MODE)
    timezone_str = get_setting("timezone", DEFAULT_TIMEZONE)
    time_format = get_setting("time_format", DEFAULT_TIME_FORMAT)
    col_phone = get_setting("col_phone", DEFAULT_COL_PHONE)
    
    try:
        tz = pytz.timezone(timezone_str)
    except:
        tz = pytz.timezone(DEFAULT_TIMEZONE)
    
    if start_str:
        poll_start = tz.localize(datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S"))
    else:
        poll_start = tz.localize(DEFAULT_POLL_START)
    
    if end_str:
        poll_end = tz.localize(datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S"))
    else:
        poll_end = tz.localize(DEFAULT_POLL_END)
    
    return poll_start, poll_end, validation_mode, tz, time_format, col_phone

def has_already_voted(phone):
    """Check if user has already submitted a response"""
    with get_db() as conn:
        result = conn.execute("SELECT phone FROM poll_results WHERE phone=?", (phone,)).fetchone()
    return result is not None

def get_employee(phone):
    """Retrieve employee information by matching phone number"""
    with get_db() as conn:
        result = conn.execute("SELECT info FROM employees WHERE phone=?", (phone,)).fetchone()
        
        if result:
            return json.loads(result[0])
        
        normalized_input = normalize_for_comparison(phone)
        all_employees = conn.execute("SELECT phone, info FROM employees").fetchall()
        for emp_phone, emp_info in all_employees:
            if normalize_for_comparison(emp_phone) == normalized_input:
                return json.loads(emp_info)
    
    return None

def save_vote(phone, response, timestamp):
    """Save a poll response"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO poll_results (phone, response, timestamp) VALUES (?, ?, ?)",
            (phone, response, timestamp)
        )
        conn.commit()

def get_total_employees():
    """Get total number of registered employees"""
    with get_db() as conn:
        result = conn.execute("SELECT COUNT(*) FROM employees").fetchone()
    return result[0] if result else 0

# --- MAIN SCREEN FUNCTIONS ---

def show_login_screen():
    """Display the login screen with tabs for staff and admin"""
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    img_path = os.path.join(script_dir, "recourses", "trans_logo.png")
    # img_path = "C:/Users/Mystic/Documents/ykk_emergency_response/recourses/trans_logo.png"
    
    try:
        img_b64 = get_base64(img_path)
        st.markdown(f"""
            <div class="header-container">
                <div class="header-logo">
                    <img src="data:image/png;base64,{img_b64}" width="120">
                </div>
                <div class="header-title">
                    <h2>Emergency Response System</h2>
                </div>
            </div>
        """, unsafe_allow_html=True)
    except Exception:
        # Fallback image fail
        st.title("🏢 YKK Emergency Response System")

    st.markdown("---")
        
    tab1, tab2 = st.tabs(["👤 Staff Login", "🔐 Admin Access"])
    
    # Get poll config for validation
    _, _, PHONE_VALIDATION_MODE, _, _, _ = get_poll_config()
    
    with tab1:
        st.subheader("Staff Emergency Login")
        st.markdown("Please enter your credentials to report your status.")
        
        with st.form("login_form"):
            phone_input = st.text_input(
                "Phone Number",
                placeholder="e.g. 03001234567",
                help="Enter your registered phone number"
            )
            pass_input = st.text_input("Password", type="password")
            submit = st.form_submit_button("Login / Report Status", type="primary", use_container_width=True)

            if submit:
                if not phone_input or not pass_input:
                    st.error("Please fill in all fields.")
                else:
                    clean = clean_phone(phone_input)
                    
                    if not validate_phone(clean, PHONE_VALIDATION_MODE):
                        st.error("Invalid phone number format. Please check and try again.")
                    elif hash_password(pass_input) != USER_PASS_HASH:
                        st.error("Invalid password.")
                    else:
                        user_info = get_employee(clean)
                        if user_info:
                            st.session_state.logged_in = True
                            st.session_state.user_phone = clean
                            st.session_state.user_info = user_info
                            st.rerun()
                        else:
                            st.error("Phone number not found in employee database.")
    
    with tab2:
        st.subheader("Administrator Access")
        st.markdown("Enter admin credentials to access the dashboard.")
        
        admin_pass = st.text_input(
            "Admin Password",
            type="password",
            help="Enter admin password to access dashboard"
        )
        
        if st.button("Login as Admin", type="primary", use_container_width=True):
            if admin_pass and hash_password(admin_pass) == ADMIN_PASS_HASH:
                st.session_state.admin_logged_in = True
                st.rerun()
            else:
                st.error("❌ Invalid admin password")
    
    # Footer
    st.markdown("---")
    st.markdown('<div style="text-align: center; color: #6c757d; font-size: 0.9rem; padding: 1rem;">Developed by PAK IT © 2026</div>', unsafe_allow_html=True)


def show_user_interface():
    """Display the user interface for logged-in staff members"""
    # Get poll configuration
    POLL_START, POLL_END, _, TZ, TIME_FORMAT, _ = get_poll_config()
    
    # Display logo and title
    script_dir = os.path.dirname(os.path.abspath(__file__))
    img_path = os.path.join(script_dir, "recourses", "trans_logo.png")
    # img_path = "C:/Users/Mystic/Documents/ykk_emergency_response/recourses/trans_logo.png"
    img_base64 = get_base64(img_path)
    
    if img_base64:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 20px;">
                <img src="data:image/png;base64,{img_base64}" width="100" style="flex-shrink: 0;">
                <h1 style="margin: 0; padding: 0; line-height: 1.2;">Staff Emergency Feedback</h1>
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.title("🏢 Staff Emergency Feedback")
    
    st.markdown("---")
    
    # Get display name
    display_name = get_display_name(st.session_state.user_info)
    
    now = datetime.now(TZ)
    remaining = POLL_END - now
    
    # Welcome message and timer
    col1, col2 = st.columns([3, 2])
    
    with col1:
        st.info(f"👤 Welcome: **{display_name}**")
    
    with col2:
        if POLL_START <= now <= POLL_END:
            days = remaining.days
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            timer_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"
            st.warning(f"🕒 **Time left:** {timer_str}")

    # Vote logic
    if now < POLL_START:
        st.warning(f"⏳ Poll opens on {format_time_display(POLL_START, TIME_FORMAT)}")
    elif now > POLL_END:
        st.error(f"🚫 This poll closed on {format_time_display(POLL_END, TIME_FORMAT)}")
    elif has_already_voted(st.session_state.user_phone):
        st.success("✅ Thank you! Your response has been recorded.")
        st.info("You can only submit one response per poll.")
    else:
        st.subheader("Please report your status, your safety is our concern.")
        st.caption("Your feedback helps us improve our YKK Pakistan working environment.")
        
        option = st.radio(
            "Choose one:",
            ["I am okay and safe.", "I am stuck but help not needed.", "I am stuck and help is needed."],
            index=None,
            help="Select the option that best represents your current status"
        )
        
        if st.button("Submit Response", type="primary", disabled=(option is None), use_container_width=True):
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            try:
                save_vote(st.session_state.user_phone, option, timestamp_str)
                st.toast("✅ Response submitted successfully!", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Error submitting response: {e}")
    
    # Logout button
    st.markdown("---")
    if st.button("🚪 Log Out", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.clear()
        st.rerun()
    
    # Footer
    st.markdown("---")
    st.markdown('<div style="text-align: center; color: #6c757d; font-size: 0.9rem; padding: 1rem;">Developed by PAK IT © 2026</div>', unsafe_allow_html=True)


def show_admin_dashboard():
    """Display the admin dashboard with full control panel"""
    st.title("🛠️ Admin Dashboard")
    
    # Logout button at top
    col1, col2 = st.columns([4, 1])
    with col1:
        st.success("✅ Logged in as Administrator")
    with col2:
        if st.button("🔒 Logout", use_container_width=True):
            # Clear all settings state on logout
            for key in list(st.session_state.keys()):
                if key.startswith('settings_'):
                    del st.session_state[key]
            st.session_state.admin_logged_in = False
            st.session_state.active_admin_tab = 0
            st.session_state.show_unsaved_dialog = False
            st.session_state.clear()
            st.rerun()
    
    st.markdown("---")
    
    # Get poll configuration
    POLL_START, POLL_END, PHONE_VALIDATION_MODE, TZ, TIME_FORMAT, COL_PHONE = get_poll_config()
    
    # Initialize settings in session state EARLY
    if (st.session_state.get('active_admin_tab', 0) == 2 or st.session_state.get('show_unsaved_dialog', False)) and 'settings_timezone' not in st.session_state:
        st.session_state.settings_timezone = str(TZ)
        st.session_state.settings_time_format = TIME_FORMAT
        st.session_state.settings_time_input = "12"
        st.session_state.settings_start_date = POLL_START.date()
        st.session_state.settings_end_date = POLL_END.date()
        st.session_state.settings_validation = PHONE_VALIDATION_MODE
        st.session_state.settings_col_phone = COL_PHONE
        
        # Initialize both 12-hour and 24-hour time values
        h12_s, m_s, p_s = convert_24hr_to_12hr(POLL_START.hour, POLL_START.minute)
        h12_e, m_e, p_e = convert_24hr_to_12hr(POLL_END.hour, POLL_END.minute)
        
        st.session_state.settings_start_hour = h12_s
        st.session_state.settings_start_min = m_s
        st.session_state.settings_start_period = p_s
        st.session_state.settings_end_hour = h12_e
        st.session_state.settings_end_min = m_e
        st.session_state.settings_end_period = p_e
        
        st.session_state.settings_start_time_24 = POLL_START.time()
        st.session_state.settings_end_time_24 = POLL_END.time()
    
    # Initialize tab state if not exists
    if 'active_admin_tab' not in st.session_state:
        st.session_state.active_admin_tab = 0
    if 'show_unsaved_dialog' not in st.session_state:
        st.session_state.show_unsaved_dialog = False
    if 'requested_tab' not in st.session_state:
        st.session_state.requested_tab = None
    if 'saving_in_progress' not in st.session_state:
        st.session_state.saving_in_progress = False
    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0
    
    # Check for unsaved changes
    has_unsaved = False
    if st.session_state.active_admin_tab == 2 and 'settings_timezone' in st.session_state:
        has_unsaved = check_settings_changed()
    
    # Unsaved changes dialog
    show_dialog = st.session_state.show_unsaved_dialog
    if show_dialog:
        st.error("### ⚠️ Unsaved Changes Detected")
        st.warning("You have unsaved changes in Settings. Please choose an action:")
        st.markdown("---")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("💾 Save & Switch", type="primary", use_container_width=True, key="save_switch"):
                success, message = save_settings_to_db()
                if success:
                    for key in list(st.session_state.keys()):
                        if key.startswith('settings_'):
                            del st.session_state[key]
                    st.session_state.active_admin_tab = st.session_state.requested_tab
                    st.session_state.show_unsaved_dialog = False
                    st.session_state.requested_tab = None
                    st.toast(f"✅ {message}", icon="✅")
                    st.rerun()
                else:
                    st.error("❌ " + message)
        
        with col2:
            if st.button("🗑️ Discard & Switch", use_container_width=True, key="discard_switch"):
                for key in list(st.session_state.keys()):
                    if key.startswith('settings_'):
                        del st.session_state[key]
                st.session_state.active_admin_tab = st.session_state.requested_tab
                st.session_state.show_unsaved_dialog = False
                st.session_state.requested_tab = None
                st.rerun()
        
        with col3:
            if st.button("❌ Cancel", use_container_width=True, key="cancel_switch"):
                st.session_state.show_unsaved_dialog = False
                st.session_state.requested_tab = None
                st.rerun()
        
        st.markdown("---")
        st.info("💡 Your current settings tab is still below - make additional changes if needed before choosing an action.")
        st.markdown("---")
    
    # Custom tab buttons
    tab_names = ["📊 View Results", "👥 Manage Users", "⚙️ Settings"]
    tab_cols = st.columns(3)
    
    for idx, (col, name) in enumerate(zip(tab_cols, tab_names)):
        with col:
            is_active = st.session_state.active_admin_tab == idx
            button_type = "primary" if is_active else "secondary"
            
            if st.button(name, key=f"tab_{idx}", type=button_type, use_container_width=True):
                if st.session_state.active_admin_tab == 2 and idx != 2:
                    if 'settings_timezone' in st.session_state and check_settings_changed():
                        st.session_state.show_unsaved_dialog = True
                        st.session_state.requested_tab = idx
                        st.rerun()
                    else:
                        st.session_state.active_admin_tab = idx
                        st.rerun()
                else:
                    st.session_state.active_admin_tab = idx
                    st.rerun()
    
    st.markdown("---")
    
    # === TAB 0: VIEW RESULTS ===
    if st.session_state.active_admin_tab == 0 and not show_dialog:
        st.subheader("Poll Results & Analytics")
        
        with get_db() as conn:
            df_results = pd.read_sql_query("SELECT * FROM poll_results", conn)
            df_emps = pd.read_sql_query("SELECT * FROM employees", conn)
        
        if not df_emps.empty:
            df_emps_expanded = pd.concat([
                df_emps.drop(['info'], axis=1),
                df_emps['info'].apply(lambda x: pd.Series(json.loads(x)))
            ], axis=1)
            
            total_employees = get_total_employees()
            total_voted = len(df_results)
            total_not_voted = total_employees - total_voted
            participation_rate = (total_voted / total_employees * 100) if total_employees > 0 else 0
            
            st.markdown("##### Participation Overview")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Staff", total_employees)
            m2.metric("Voted", total_voted)
            m3.metric("Not Voted", total_not_voted)
            m4.metric("Turnout", f"{participation_rate:.1f}%")
            
            if not df_results.empty:
                final_df = pd.merge(df_results, df_emps_expanded, on="phone", how="left")
                
                st.markdown("##### Sentiment Analysis")
                counts = final_df['response'].value_counts()
                                    
                fig = px.pie(
                    values=counts.values, 
                    names=counts.index,
                    color=counts.index,
                    color_discrete_map={
                        'I am okay and safe.': '#2ecc71',
                        'I am stuck but help not needed.': '#f39c12',
                        'I am stuck and help is needed.': '#e74c3c'
                    },
                    hole=0.4
                )
                fig.update_layout(margin=dict(t=20, b=20, l=0, r=0), height=350)
                st.plotly_chart(fig, use_container_width=True)

                st.markdown("##### Search Responses")
                search_query = st.text_input("🔍 Find by name or phone...", placeholder="Type to search...")
                
                display_df = final_df.astype(str)
                if search_query:
                    display_df = display_df[display_df.apply(lambda row: row.astype(str).str.contains(search_query, case=False).any(), axis=1)]
                
                st.dataframe(display_df, use_container_width=True, height=400)

                ok_count = len(df_results[df_results['response'] == 'I am okay and safe.'])
                not_ok_count = len(df_results[df_results['response'] == 'I am stuck but help not needed.'])
                help_needed_count = len(df_results[df_results['response'] == 'I am stuck and help is needed.'])
            else:
                st.info("📭 No responses received yet.")
                final_df = pd.DataFrame()
                ok_count = not_ok_count = help_needed_count = 0

            st.markdown("##### Export Data")
            col1, col2 = st.columns(2)
            
            with col1:
                if not df_results.empty:
                    excel_buffer_voted = BytesIO()
                    with pd.ExcelWriter(excel_buffer_voted, engine='openpyxl') as writer:
                        final_df.to_excel(writer, sheet_name='Voted Employees', index=False)
                        
                        summary_data = {
                            'Metric': ['Total Employees', 'Total Voted', 'Participation Rate', 
                                      'OK Responses', 'Stuck (No Help)', 'Help Needed'],
                            'Value': [total_employees, total_voted, f"{participation_rate:.1f}%",
                                     ok_count, not_ok_count, help_needed_count]
                        }
                        summary_df = pd.DataFrame(summary_data)
                        summary_df.to_excel(writer, sheet_name='Summary', index=False)
                    
                    excel_buffer_voted.seek(0)
                    st.download_button(
                        "✅ Download Voted Employees",
                        excel_buffer_voted,
                        f"voted_employees_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                else:
                    st.button("✅ Download Voted Employees", disabled=True, use_container_width=True)
            
            with col2:
                voted_phones = set(df_results['phone'].tolist()) if not df_results.empty else set()
                not_voted_df = df_emps_expanded[~df_emps_expanded['phone'].isin(voted_phones)]
                
                if not not_voted_df.empty:
                    excel_buffer_not_voted = BytesIO()
                    with pd.ExcelWriter(excel_buffer_not_voted, engine='openpyxl') as writer:
                        not_voted_df.to_excel(writer, sheet_name='Not Voted Employees', index=False)
                    
                    excel_buffer_not_voted.seek(0)
                    st.download_button(
                        "❌ Download Not Voted Employees",
                        excel_buffer_not_voted,
                        f"not_voted_employees_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                else:
                    st.success("🎉 All employees have voted!")
        else:
            st.warning("⚠️ No employee data available. Please upload employee data in 'Manage Users' tab.")
    
    # === TAB 1: MANAGE USERS ===
    elif st.session_state.active_admin_tab == 1 and not show_dialog:
        st.subheader("Employee Database Management")
        st.info(f"Current phone column: **{COL_PHONE}** (change in Settings if needed)")
        
        uploaded_file = st.file_uploader(
            "📁 Upload Excel File (.xlsx)",
            type=["xlsx"],
            key=f"uploader_{st.session_state.uploader_key}",
            help=f"File must contain a '{COL_PHONE}' column"
        )
        
        if uploaded_file:
            try:
                df_upload = pd.read_excel(uploaded_file, dtype=str)
                
                if COL_PHONE not in df_upload.columns:
                    st.error(f"❌ Missing '{COL_PHONE}' column")
                    st.info(f"💡 Available columns: {', '.join(df_upload.columns)}")
                    st.warning("Change the phone column name in Settings if needed.")
                else:
                    df_upload[COL_PHONE] = df_upload[COL_PHONE].apply(clean_phone)
                    
                    invalid_phones = df_upload[~df_upload[COL_PHONE].apply(lambda x: validate_phone(x, PHONE_VALIDATION_MODE))]
                    
                    if not invalid_phones.empty:
                        with st.expander(f"⚠️ {len(invalid_phones)} invalid phone numbers found", expanded=False):
                            st.dataframe(invalid_phones[[COL_PHONE]], use_container_width=True)
                    
                    valid_df = df_upload[df_upload[COL_PHONE].apply(lambda x: validate_phone(x, PHONE_VALIDATION_MODE))]
                    
                    st.success(f"✅ Ready to upload {len(valid_df)} employees")
                    st.dataframe(valid_df.head(10), use_container_width=True)
                    
                    if st.button("🔥 Confirm Upload & Overwrite Database", type="primary"):
                        with get_db() as conn:
                            conn.execute("DELETE FROM employees")
                            for _, row in valid_df.iterrows():
                                phone = row[COL_PHONE]
                                info_dict = row.drop(COL_PHONE).to_dict()
                                
                                for key, value in info_dict.items():
                                    if pd.isna(value):
                                        info_dict[key] = None
                                    elif isinstance(value, (pd.Timestamp, datetime)):
                                        info_dict[key] = value.strftime('%Y-%m-%d')
                                
                                info_json = json.dumps(info_dict)
                                conn.execute(
                                    "INSERT OR IGNORE INTO employees (phone, info) VALUES (?, ?)",
                                    (phone, info_json)
                                )
                            conn.commit()
                        
                        st.session_state.uploader_key += 1
                        st.toast("✅ Database updated successfully!", icon="✅")
                        st.rerun()
            
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")
        
        st.markdown("---")
        st.markdown("##### Current Employee Database")
        with get_db() as conn:
            current_emps = pd.read_sql_query("SELECT * FROM employees", conn)
        
        if not current_emps.empty:
            df_preview = pd.concat([
                current_emps.drop(['info'], axis=1),
                current_emps['info'].apply(lambda x: pd.Series(json.loads(x)))
            ], axis=1)
            st.dataframe(df_preview.astype(str), use_container_width=True, height=400)
            st.caption(f"Total: {len(current_emps)} employees")
        else:
            st.warning("⚠️ No employees in database yet.")
    
    # === TAB 2: SETTINGS ===
    if st.session_state.active_admin_tab == 2 or show_dialog:
        st.subheader("Application Configuration")
        
        if check_settings_changed():
            st.warning("⚠️ **You have unsaved changes.** Click 'Save All Settings' below to apply them.")
        
        # Timezone
        st.markdown("**Timezone**")
        common_timezones = ["Asia/Karachi", "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", 
                           "Europe/London", "Europe/Paris", "US/Eastern", "US/Pacific", "UTC"]
        current_tz_str = st.session_state.settings_timezone
        if current_tz_str not in common_timezones: 
            common_timezones.insert(0, current_tz_str)
        
        st.selectbox("Select Timezone", options=common_timezones, 
                     index=common_timezones.index(current_tz_str),
                     key="settings_timezone")

        # Time format
        st.markdown("**Time Display Format**")
        st.radio("Display format:", options=["12", "24"],
                 format_func=lambda x: "12-hour (AM/PM)" if x == "12" else "24-hour",
                 index=0 if st.session_state.settings_time_format == "12" else 1,
                 horizontal=True,
                 key="settings_time_format")

        # Poll schedule
        st.markdown("**Poll Schedule**")
        
        prev_time_input = st.session_state.get('prev_time_input', "12")
        
        time_input = st.radio("Time input:", options=["12", "24"], horizontal=True,
                 format_func=lambda x: "12-hour" if x == "12" else "24-hour",
                 key="settings_time_input")
        
        # Ensure 12-hour fields exist
        if 'settings_start_hour' not in st.session_state:
            h12_s, m_s, p_s = convert_24hr_to_12hr(
                st.session_state.settings_start_time_24.hour,
                st.session_state.settings_start_time_24.minute
            )
            st.session_state.settings_start_hour = h12_s
            st.session_state.settings_start_min = m_s
            st.session_state.settings_start_period = p_s
        
        if 'settings_end_hour' not in st.session_state:
            h12_e, m_e, p_e = convert_24hr_to_12hr(
                st.session_state.settings_end_time_24.hour,
                st.session_state.settings_end_time_24.minute
            )
            st.session_state.settings_end_hour = h12_e
            st.session_state.settings_end_min = m_e
            st.session_state.settings_end_period = p_e
        
        # Sync values when format changes
        if time_input != prev_time_input:
            if time_input == "24":
                h, m = convert_12hr_to_24hr(
                    st.session_state.settings_start_hour,
                    st.session_state.settings_start_min,
                    st.session_state.settings_start_period
                )
                st.session_state.settings_start_time_24 = time(h, m)
                
                h, m = convert_12hr_to_24hr(
                    st.session_state.settings_end_hour,
                    st.session_state.settings_end_min,
                    st.session_state.settings_end_period
                )
                st.session_state.settings_end_time_24 = time(h, m)
            else:
                h12, m, p = convert_24hr_to_12hr(
                    st.session_state.settings_start_time_24.hour,
                    st.session_state.settings_start_time_24.minute
                )
                st.session_state.settings_start_hour = h12
                st.session_state.settings_start_min = m
                st.session_state.settings_start_period = p
                
                h12, m, p = convert_24hr_to_12hr(
                    st.session_state.settings_end_time_24.hour,
                    st.session_state.settings_end_time_24.minute
                )
                st.session_state.settings_end_hour = h12
                st.session_state.settings_end_min = m
                st.session_state.settings_end_period = p
            
            st.session_state.prev_time_input = time_input

        col1, col2 = st.columns(2)
        with col1:
            st.date_input("Start Date", format="DD/MM/YYYY", key="settings_start_date")
            
            if st.session_state.settings_time_input == "24":
                st.time_input("Start Time", key="settings_start_time_24")
            else:
                st_col1, st_col2, st_col3 = st.columns([2, 2, 1])
                with st_col1:
                    st.number_input("Hour", 1, 12, key="settings_start_hour")
                with st_col2:
                    st.number_input("Minute", 0, 59, key="settings_start_min")
                with st_col3:
                    st.selectbox("", ["AM", "PM"], 
                                index=0 if st.session_state.settings_start_period == "AM" else 1, 
                                key="settings_start_period")

        with col2:
            st.date_input("End Date", format="DD/MM/YYYY", key="settings_end_date")
            
            if st.session_state.settings_time_input == "24":
                st.time_input("End Time", key="settings_end_time_24")
            else:
                et_col1, et_col2, et_col3 = st.columns([2, 2, 1])
                with et_col1:
                    st.number_input("Hour ", 1, 12, key="settings_end_hour")
                with et_col2:
                    st.number_input("Minute ", 0, 59, key="settings_end_min")
                with et_col3:
                    st.selectbox(" ", ["AM", "PM"], 
                                index=0 if st.session_state.settings_end_period == "AM" else 1, 
                                key="settings_end_period")

        # Phone column
        st.markdown("**Phone Column Name**")
        st.text_input("Excel column name", key="settings_col_phone",
                     help="Exact column name in Excel with phone numbers")
        
        # Phone validation
        st.markdown("**Phone Validation**")
        st.selectbox("Validation mode", ["flexible", "strict"], 
                     index=0 if st.session_state.settings_validation == "flexible" else 1,
                     key="settings_validation",
                     help="Strict: Pakistan only | Flexible: International")
        
        # Save/Reset buttons
        col1, col2 = st.columns([2, 1])
        with col1:
            save_disabled = st.session_state.saving_in_progress
            if st.button("💾 Save All Settings", type="primary", use_container_width=True, 
                        disabled=save_disabled, key="save_settings_btn"):
                st.session_state.saving_in_progress = True
                success, message = save_settings_to_db()
                if success:
                    st.toast(f"✅ {message}", icon="✅")
                    st.session_state.saving_in_progress = False
                    for key in list(st.session_state.keys()):
                        if key.startswith('settings_'):
                            del st.session_state[key]
                    st.rerun()
                else:
                    st.session_state.saving_in_progress = False
                    st.error("❌ " + message)
        
        with col2:
            if st.button("🔄 Reset", use_container_width=True):
                if st.session_state.get('confirm_reset_settings', False):
                    with get_db() as conn:
                        conn.execute("DELETE FROM settings")
                        conn.commit()
                    for key in list(st.session_state.keys()):
                        if key.startswith('settings_'):
                            del st.session_state[key]
                    st.session_state.confirm_reset_settings = False
                    st.toast("✅ Settings reset to defaults!", icon="✅")
                    st.rerun()
                else:
                    st.session_state.confirm_reset_settings = True
                    st.warning("⚠️ Click again to confirm")
        
        st.markdown("---")
        st.markdown("##### Advanced Operations")
        st.warning("**⚠️ Warning:** The actions below are permanent and cannot be undone.")
        
        # Clear responses
        confirm_resp = st.checkbox("Confirm: Delete all responses (keeps employees)")
        if st.button("🗑️ Clear Responses", type="secondary", disabled=not confirm_resp):
            with get_db() as conn:
                conn.execute("DELETE FROM poll_results")
                conn.commit()
            st.toast("✅ All responses cleared!", icon="✅")
            st.rerun()
        
        # Complete reset
        confirm_all = st.checkbox("Confirm: Delete EVERYTHING (responses + employees)")
        if st.button("💀 Reset Database", type="secondary", disabled=not confirm_all):
            with get_db() as conn:
                conn.execute("DELETE FROM poll_results")
                conn.execute("DELETE FROM employees")
                conn.commit()
            st.toast("✅ Database completely reset!", icon="✅")
            st.rerun()
    
    # Footer
    st.markdown("---")
    st.markdown('<div style="text-align: center; color: #6c757d; font-size: 0.9rem; padding: 1rem;">Developed by PAK IT © 2026</div>', unsafe_allow_html=True)


# --- UI CONFIGURATION ---
st.set_page_config(
    page_title="YKK Emergency Poll",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# --- CUSTOM CSS ---
st.markdown("""
<style>
    :root { color-scheme: dark; }
    [data-testid="stAppViewContainer"] { background-color: #0e1117; }
    
    /* THE FIX: Flexbox container for logo and title */
    .header-container {
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        gap: 20px !important;
        margin-bottom: 20px !important;
    }

    .header-container h1 {
        margin: 0 !important;
        padding: 0 !important;
        line-height: 1 !important;
    }

    /* Ensure success/info boxes remain readable in Dark Mode */
    [data-testid="stNotificationContentInfo"] p, 
    [data-testid="stNotificationContentSuccess"] p {
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)

# Initialize database
init_db()

# Initialize session state
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'admin_logged_in' not in st.session_state:
    st.session_state.admin_logged_in = False

# ====================================
# MAIN APP ROUTER
# ====================================

if st.session_state.get('admin_logged_in'):
    show_admin_dashboard()

elif st.session_state.get('logged_in'):
    show_user_interface()

else:

    show_login_screen()

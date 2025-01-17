import os
import json

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from dotenv_vault import load_dotenv
from flask import Flask, flash, get_flashed_messages, jsonify, redirect, render_template, url_for, request, session, g
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Integer, String, insert, select
from sqlalchemy.orm import DeclarativeBase, Mapped, class_mapper, mapped_column, relationship
from sqlalchemy.exc import IntegrityError
from typing import List
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import decimal, login_required, percent, usd  

load_dotenv()

# Configure app
app = Flask(__name__)
app.jinja_env.filters["usd"] = usd
app.jinja_env.filters["percent"] = percent
app.config['DEBUG'] = True  # Enable debug mode


# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
app.secret_key = os.getenv("SECRET_KEY")

Session(app)

if __name__ == "__main__":
    app.run(debug=True)

# Configure SQL database
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///wisp.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    loans = db.relationship('Loans', back_populates='user', cascade='all, delete-orphan')
    simulated = db.relationship('Simulated', back_populates='user', cascade='all, delete-orphan')
    plans = db.relationship('Plans', back_populates='user')

class Loans(db.Model):
    __tablename__ = 'loans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    interest = db.Column(db.Integer, nullable=False)
    monthly_interest = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user = db.relationship('User', back_populates='loans')
    simulated = db.relationship('Simulated', back_populates='loans')
    plan_payments = db.relationship('Plan_payments', back_populates='loans')


class Simulated(db.Model):
    __tablename__ = 'simulated'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(50), nullable=False)
    balance = db.Column(db.Integer, nullable=False)
    monthly_interest = db.Column(db.Integer, nullable=False)
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id'))
    label = db.Column(db.String(50), nullable=False)
    loans = db.relationship('Loans', back_populates='simulated')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user = db.relationship('User', back_populates='simulated')

class Plans(db.Model):
    __tablename__ = 'plans'
    # Plan table should have the id of each plan, plan name, start date, end date
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    name = db.Column(db.String(50), nullable=False, unique=True)
    start_date = db.Column(db.String(50), nullable=False)
    end_date = db.Column(db.String(50), nullable=False)
    # Should merge with a table that has all payments
    plan_payments = db.relationship('Plan_payments', back_populates='plans', cascade='all, delete-orphan')
    user = db.relationship('User', back_populates='plans')

class Plan_payments(db.Model):
    __tablename__ = 'plan_payments'
    # plan payments should have id of each payment, which plan id its related to, loan id its making payment to related to the simulated table
    # date of payment, and payment amount
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'))
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id'))
    date = db.Column(db.String(50), nullable=False)
    payment = db.Column(db.Integer, nullable=False)
    plans = db.relationship('Plans', back_populates='plan_payments')
    loans = db.relationship('Loans', back_populates='plan_payments')


with app.app_context():
    db.create_all()
    
@app.before_request
def before_request():
    session.modified = True
    g.flashed_messages = get_flashed_messages(with_categories=True)

@app.after_request
def after_request(response):
    """Ensure responses aren't cached"""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response

@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Show each section overview, homepage"""
    if request.method == "GET":
        name = get_name(session["user_id"])
    return render_template("index.html", name=name)

@app.route("/loans", methods=["GET", "POST"])
@login_required
def loans():
    loans = get_loans(session["user_id"])
    return render_template("loans.html", loans=loans, usd=usd, percent=percent, decimal=decimal, total=get_total(loans), interest=get_interest(loans))

@app.route("/manage-loans", methods=["GET"])
@login_required
def manage_loans():
    set_form_name("s-m")
    if request.method == "GET":
        loans = db.session.scalars(select(Loans).where(Loans.user_id == session["user_id"])).all()
        total = 0
        interest = 0

        for loan in loans:
            total += loan.amount
            interest += loan.monthly_interest

        return render_template("manage-loans.html", loans=loans, usd=usd, percent=percent, decimal=decimal, total=total, interest=interest)


@app.route("/budget", methods=["GET", "POST"])
@login_required
def budget():
    return render_template("budget.html")

@app.route("/progress", methods=["GET", "POST"])
@login_required
def progress():
    return render_template("progress.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    session.clear()
    set_form_name("login-form")
    if request.method == "POST":
        if not request.form.get("username"):
            flash("Enter username", "danger")
            return redirect("/login")
        elif not request.form.get("password"):
            flash("Enter password", "danger")
            return redirect("/login")
        
        username = request.form.get("username")
        entered_password = request.form.get("password")
        result = db.session.execute(select(User).where(User.username == username))
        copies = 0
        username_real = False
        for user_obj in  result.scalars():
            if username == user_obj.username:
                username_real = True
                copies += 1
        if not username_real:
            flash("Username doesn't exist", "danger")
            return redirect("/login")
        hash = db.session.scalar(select(User.password).where(User.username == username))

        if username_real and copies == 1 and check_password_hash(hash, entered_password):
            session["user_id"] = db.session.scalar(select(User.id).where(User.username == username and User.password == hash))
            flash(f"You're logged in as {username}", "success")
            return redirect("/")
        else:
            flash("Password incorrect", "danger")
            return redirect("/login")

    else:
        return render_template("login.html")

@app.route("/account", methods=["GET"])
@login_required
def account():
    name = get_name(session["user_id"])
    username = get_username(session["user_id"])
    return render_template("account.html", name=name, username=username)

@app.route("/update-password", methods=["POST"])
@login_required
def update_password():
    flash("Password updated", "success")
    set_form_name("update-password-form")
    return redirect("/account")

@app.route("/update-username", methods=["POST"])
@login_required
def update_username():
    flash("Username updated to", "success")
    set_form_name("update-username-form")
    return redirect("/account")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    session.clear()
    set_form_name("signup-form")

    if request.method == "POST":
        if not request.form.get("username") or not request.form.get("name") or not request.form.get("password") or not request.form.get("confirm"):
            flash("Please enter all fields", "danger")
            return redirect("/signup")
        
        username = request.form.get("username")
        name = request.form.get("name")
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        name = name.capitalize()
        username = username.lower()

        if check_spaces(username):
            flash("Spaces not allowed in username", "danger")
            return redirect("/signup")
        
        if check_spaces(password):
            flash("Spaces not allowed in password", "danger")
            return redirect("/signup")
        
        if password != confirm:
            flash("Passwords don't match", "danger")
            return redirect("/signup")
        
        # if len(password) < 10:
        #     flash("Password length must be 10+", "danger")
        #     return redirect("/signup")
        
        try:
            user = User(name=name, username=username, password=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            flash(f"Congrats {name}, registration success!", "success")
            session["user_id"] = db.session.scalar(select(User.id).where(User.username == username))
            return redirect("/")
        
        except IntegrityError:
            flash("Username already exists, try logging in instead", "danger")
            return redirect("/login")

    return render_template("signup.html")

@app.route("/signout")
def signout():
    session.clear()
    return redirect("/login")

@app.route("/add-loan", methods=["POST", "GET"])
@login_required
def add_loan():

    if request.method == "POST":
        loan = get_loan("add", session["user_id"])
        set_form_name("add-loan-form")
        if isinstance(loan, Loans):
            db.session.add(loan)
            db.session.commit()
            flash(f"{loan.name} added successfully!", "success")
            return redirect("/manage-loans")
        else:
            url = loan
            return redirect(url)
    
    else:
        loans = get_loans(session["user_id"])
        return render_template("add-loan-form.html", usd=usd, loans=loans, percent=percent, total=get_total(loans), interest=get_interest(loans), decimal=decimal)

@app.route("/edit-loan", methods=["POST", "GET"])
@login_required
def edit_loan():

    set_form_name("edit-loan-form")

    if request.method == "POST":
        if not request.form.get("edit-selected-loan"):
            flash("Please select loan to edit", "warning")
            return redirect("/edit-loan")
        
        edit_loan_id = request.form.get("selected-option-id")
        selected_loan = db.session.execute(select(Loans).where(Loans.id == edit_loan_id)).scalar()
        updated_name = selected_loan.name

        if not request.form.get("edit-name") and not request.form.get("edit-amount") and not request.form.get("edit-interest"):
            flash("No changes made, please enter at least one field", "warning")
            return redirect("/edit-loan")
        
        if request.form.get("edit-name"):
            selected_loan.name = request.form.get("edit-name")
            flash(f"{updated_name} name updated", "success")
        if request.form.get("edit-amount"):
            new_amount = request.form.get("edit-amount")
            try:
                selected_loan.amount = float(new_amount)
                flash(f"{updated_name} balance updated", "success")
            except ValueError:
                flash(f"{updated_name} balance not updated, enter number only", "danger")
        if request.form.get("edit-interest"):
            new_interest = request.form.get("edit-interest")
            try:
                selected_loan.interest = float(new_interest)
                flash(f"{updated_name} interest updated", "success")
            except ValueError:
                flash(f"{updated_name} interest not updated, enter number only", "danger")
        update_monthly_interest(selected_loan)
        db.session.commit()
        return redirect("/edit-loan")

    else:
        loans = get_loans(session["user_id"])
        return render_template("edit-loan-form.html", usd=usd, loans=loans, percent=percent, total=get_total(loans), interest=get_interest(loans), decimal=decimal)

@app.route("/delete-loan", methods=["POST", "GET"])
@login_required
def delete_loan():
    set_form_name("delete-loan-form")

    if request.method == "POST":

        if not request.form.get("delete-selected-loan"):
            flash("Must select loan to delete", "warning")
            return redirect("/delete-loan")
        
        delete_loan_id = request.form.get("selected-option-id")

        delete_loan = db.session.scalar(select(Loans).where(Loans.id == delete_loan_id))
        db.session.delete(delete_loan)
        db.session.commit()
        flash(f"{delete_loan.name} deleted successfully", "success")
        return redirect("/manage-loans")
        
    else:
        loans = get_loans(session["user_id"])
        return render_template("delete-loan-form.html", usd=usd, loans=loans, percent=percent, total=get_total(loans), interest=get_interest(loans), decimal=decimal)

@app.route("/make-payment", methods=["POST", "GET"])
@login_required
def make_payment():
    set_form_name("make-payment-form")
    if request.method == "GET":
        loans = get_loans(session["user_id"])
        return render_template("make-payment-form.html", usd=usd, loans=loans, percent=percent, total=get_total(loans), interest=get_interest(loans), decimal=decimal)
       
    else:
        if not request.form.get("payment-selected-loan"):
            flash("Please select loan", "warning")
            return redirect("/make-payment")
        if not request.form.get("payment-amount"):
            flash("Please enter payment amount", "warning")
            return redirect("/make-payment")
        
        payment_loan_id = request.form.get("selected-option-id")
        payment_loan = db.session.scalar(select(Loans).where(Loans.id == payment_loan_id))
        payment_loan.amount = payment_loan.amount - float(request.form.get("payment-amount"))
        if payment_loan.amount < 0:
            flash("Payment amount cannot exceed loan balance", "danger")
            return redirect("/make-payment")
        else:
            update_monthly_interest(payment_loan)
            db.session.commit()
        return redirect("/make-payment")
    
@app.route("/simulate-payments", methods=["GET", "POST"])
@login_required
def simulate_payments():
    set_form_name("simulate-payments-form")
    loans = get_loans(session["user_id"])

    # Create sim_loans dict, get balance and interest balance of all loans
    sim_loans = dict()
    for loan in loans:
        sim_loans[loan.id] = {"interest": loan.interest, "monthly_interest": round(loan.monthly_interest, 2), "balance": loan.amount, "name": loan.name}

# Testing strategies here
# End testing strategies

    if request.method == "GET":
        return render_template("simulate-payments.html", usd=usd, loans=loans, percent=percent, total=get_total(loans), interest=get_interest(loans), decimal=decimal)
   
    else:
        if not request.form.get("simulate-amount") or not request.form.get("simulate-frequency") or not request.form.get("simulate-strategy") or not request.form.get("simulate-duration"):
            flash("All fields required", "danger")
            return redirect("/simulate-payments")

        sim_frequency = int(request.form.get("simulate-frequency"))
        sim_strategy = request.form.get("simulate-strategy")
        
        try:
            sim_payment = float(request.form.get("simulate-amount"))
        except ValueError:
            flash("Amount must be number", "danger")
            return redirect("/simulate-payments")
            
        try:
            sim_duration = int(request.form.get("simulate-duration"))
        except ValueError:
            flash("Duration must be number", "danger")
            return redirect("/simulate-payments")
        if sim_duration <= 0:
            flash("Enter 1 or more months", "danger")
            return redirect("/simulate-payments")
            
        delete_simulated()

        sim_list = list(sim_loans.items())

        # Every month:
        for m in range(sim_duration):
            # Add sim loans to table
            d = date.today()
            add_sim_data(sim_list, d + relativedelta(months=+m))
            # add_sim_data(sim_loans, date.today() + timedelta(weeks=+(month * 4)))
            # Make min payments:
            for payment in range(sim_frequency):
                print(f"Payment #{payment + 1}")
                funds = sim_payment
                # Pay monthly interest / payments per month to all loans
                for id, loan in sim_list:
                    paid = (loan["monthly_interest"] / sim_frequency)
                    print(f"Paid = {paid}")
                    funds -= paid
                    if funds < 0:
                        print(f"Funds = {funds}")
                        flash("Payment must meet minimum payment", "danger")
                        return redirect("/simulate-payments")
                    loan["balance"] -= paid
                    loan["monthly_interest"] = (loan["balance"] * (loan["interest"] / 100)) / 12
                
                if sim_strategy == "avalanche":
                    avalanche(sim_loans, funds)
    
            

            # End of month add interest
            for id, loan in sim_list:
                loan["balance"] += loan["monthly_interest"]

            db.session.commit()
        

        # Expand
        if sim_strategy == "snowball":
            pass
        if sim_strategy == "weighted":
            pass
        return redirect("/simulate-payments")

if __name__ == "__main__":
    db.create_all()
    app.run(debug=True)


def check_spaces(string):
    if " " in string:
        return True
    else:
        return False  

def get_name(user_id):
    name = db.session.execute(select(User.name).where(user_id == User.id)).scalar()
    return name

def get_username(user_id):
    username = db.session.execute(select(User.username).where(user_id == User.id)).scalar()
    return username

def get_loans(user_id):
    loans = db.session.scalars(select(Loans).where(Loans.user_id == user_id)).all()
    return loans

def get_total(loans):
    total = 0

    for loan in loans:
        total += loan.amount
    
    return total

def get_interest(loans):
    interest = 0
    for loan in loans:
        interest += loan.monthly_interest
    
    return interest

def get_loan(form, user_id):
    response = "/" + form + "-loan"
    responding = True
    while responding:       
        if not request.form.get(form + "-name") or not request.form.get(form + "-amount") or not request.form.get(form + "-interest"):
            flash("All fields required", "danger")
            responding = False
            break
            
        name = request.form.get(form + "-name")
        amount = request.form.get(form + "-amount")
        interest = request.form.get(form + "-interest")

        try:
            amount = float(amount)
            interest = float(interest)
            monthly_interest = ((amount * (interest / 100)) / 12)
        
        except ValueError:
            if type(amount) != int:
                flash("Enter dollar amount of loan", "danger")  
                responding = False              
                break            
            if type(interest) != int:
                flash("Enter interest percentage of loan", "danger")
                responding = False
                break 
            else:
                flash("Unexpected input value", "danger")
                responding = False
                break
        
        loan = Loans(name=name, amount=amount, interest=interest, monthly_interest=monthly_interest, user_id=user_id)
        response = loan
        responding = False
        break
    return response

def set_form_name(form_name):
    session["form_name"] = form_name

def update_monthly_interest(loan):
    loan.monthly_interest = (loan.amount * (loan.interest / 100)) / 12

def delete_simulated():
    simulated_loans = db.session.scalars(select(Simulated).where(Simulated.user_id == session["user_id"]))
    for loan in simulated_loans:
        db.session.delete(loan)
    db.session.commit()

def sim_table_to_dict(sim_table):
    sim_dict = dict()
    for sim in sim_table:
        sim_dict[sim.id] = ({"date": sim.date, "balance": sim.balance, "label": sim.label})

    
    json_str = json.dumps(sim_dict, indent=4)
    json_dict = jsonify(sim_dict)
    return json_dict

@app.route("/retrieve-sim-data")
@login_required
def retrieve_sim_data():
    simmed_loans = db.session.scalars(select(Simulated).where(Simulated.user_id == session["user_id"]))
    sim_dict = sim_table_to_dict(simmed_loans)

    return sim_dict

@app.route("/retrieve-loans")
@login_required
def retrieve_loans():
    loans = db.session.scalars(select(Loans.name).where(Loans.user_id == session["user_id"]))
    name_list = []
    for name in loans:
        name_list.append(name)    
    return name_list     

def get_highest_interest_id(loan_list):
    highest_id = None
    highest_interest = 0
    for id, loan in loan_list:
        if loan["monthly_interest"] > highest_interest:
            highest_id = id
            highest_interest = loan["monthly_interest"]
    # print(highest_id)
    return highest_id

def add_sim_data(loan_list, date):
    for id, loan in loan_list:
        simulated_loan = Simulated(date=date, balance=round(loan["balance"], 2), monthly_interest=loan["monthly_interest"], loan_id=id, label=loan["name"], user_id=session["user_id"])
        db.session.add(simulated_loan)


# Return false if can't calculate payments
def calculate_sim_payments(sim_loans, strategy, payment, frequency):
    pass

def make_sim_payments(sim_list):
    pass

def avalanche(sim_loans, funds):
    sim_list = list(sim_loans.items())
    # While payment is greater than 0
    while True:
        # Get highest interest loan
        highest_id = get_highest_interest_id(sim_list)
        if highest_id == None:
            break
        highest_loan = sim_loans[highest_id]
        print(f"Highest loan = {highest_loan}\nBalance = {highest_loan["balance"]}\n")
        # If highest interest loan's balance is less than payment amount
        if highest_loan["balance"] < funds:
            paid = highest_loan["balance"]
            # Just pay balance and get next highest interest loan
            highest_loan["balance"] -= paid
            highest_loan["monthly_interest"] = (highest_loan["balance"] * (highest_loan["interest"] / 100)) / 12
            funds -= paid
        # Else put all of that payment towards the loan and go to next payment
        else:
            paid = funds
            highest_loan["balance"] -= paid
            highest_loan["monthly_interest"] = (highest_loan["balance"] * (highest_loan["interest"] / 100)) / 12
            break

        if highest_loan["balance"] <= 0:
            print("Paid off")
            break
        

# Create function to calculate length of time till all loans paid off
from email.mime import application, message
import json
import logging
import random
import asyncio
import types
from urllib import response
import requests
import time
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from mnemonic import Mnemonic
import concurrent.futures
import httpx

try:
    with open("db.json", "r") as f:
        data = json.load(f)
        user_invoices.update(data.get("user_invoices", {}))
        user_orders.update(data.get("user_orders", {}))
        user_pro_status.update(data.get("user_pro_status", {}))
        pro_expiry.update(data.get("pro_expiry", {}))
        total_wallets_checked.update(data.get("total_wallets_checked", {}))
except FileNotFoundError:
    pass


# ─── CONFIG ────────────────────────────────────────────────────────────────
TOKEN = "8108098227:AAHZn-1VGTeDsHWQu3G57gX9FTYQEFpIh1E"
NOWPAY_API_KEY = "VK9ZXBG-SW3ME98-MTVJ106-HM3Z8G4"
logging.basicConfig(level=logging.INFO)

# ─── STATE ──────────────────────────────────────────────────────────────────
user_tasks = {}
user_invoices = {}
user_last_seen = {}     # user_id: timestamp
all_users = set()       # user_id list
user_pro_status = {}
pro_expiry = {}
user_payments = {}  # Example: {user_id: payment_id}
user_orders = {}
total_wallets_checked = {}
user_referrer = {}
user_referred_count = {}
user_daily_bonus_claim = {}
user_available_checks = {}
user_daily_checks = {}
user_bonus_balance = {}
user_withdraw_requests = {}



# ─── BLOCKCHAIN CONFIG ─────────────────────────────────────────────────────
API_CONFIG = {
    "ETH": {"url": "https://api.etherscan.io/api", "key": "ETH_API_KEY"},
    "BNB": {"url": "https://api.bscscan.com/api", "key": "BNB_API_KEY"},
    "Polygon": {"url": "https://api.polygonscan.com/api", "key": "POLYGON_API_KEY"},
    "Optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": "OPTIMISM_API_KEY"},
}






# ─── HELPERS ────────────────────────────────────────────────────────────────
def generate_real_mnemonic():
    return Mnemonic("english").generate(strength=128)

def generate_fake_address():
    return "0x" + ''.join(random.choices("abcdef0123456789", k=40))

def is_pro(user_id):
    return user_pro_status.get(user_id, False) and pro_expiry.get(user_id, 0) > time.time()

async def check_payment_status(user_id, payment_id, context):
    headers = {"x-api-key": NOWPAY_API_KEY}
    for _ in range(18):
        try:
            if not payment_id:
                await context.bot.send_message(chat_id=user_id, text="❌ No invoice found for your account.")
                return

            url = f"https://api.nowpayments.io/v1/payment/{payment_id}"
            response = requests.get(url, headers=headers)
            data = response.json()

            # Debug
            print("DEBUG: Invoice Response:", data)

            status = data.get("payment_status")
            print(f"Invoice status: {status}")

            if status == "finished":
                user_pro_status[user_id] = True
                pro_expiry[user_id] = time.time() + 30 * 24 * 3600
                await context.bot.send_message(chat_id=user_id, text="✅ Payment confirmed! You are now Pro for 30 days.")
                return
            else:
                await context.bot.send_message(chat_id=user_id, text="❌ No payment found or it's still pending.")
        except Exception as e:
            logging.error(f"Payment check failed for {user_id}: {e}")
        await asyncio.sleep(10)

    await context.bot.send_message(chat_id=user_id, text="❌ Payment not confirmed in time. Please try again later.")

async def create_payment(update, context, user_id, coin):
    chat_id = update.effective_chat.id
    headers = {"x-api-key": NOWPAY_API_KEY}
    payload = {
        "price_amount": 10,  # Set dynamically if needed
        "price_currency": "usd",
        "pay_currency": coin.lower(),
        "order_id": str(user_id),
        "order_description": f"Pro Upgrade for user {user_id}",
        "ipn_callback_url": "https://yourdomain.com/ipn"  # Optional: for backend payment detection
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.nowpayments.io/v1/invoice", headers=headers, json=payload)
            data = response.json()

            if "invoice_id" in data:
                invoice_id = data["invoice_id"]
                user_invoices[user_id] = invoice_id
                user_orders[invoice_id] = user_id  # 💾 Reverse lookup
                save_stats()  # ⬅️ persist to file immediately


        except Exception as e:
            logging.error(f"HTTP error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="❌ Error while creating payment. Please try again.")
            return

    if "invoice_url" in data:
        payment_url = data["invoice_url"]


        keyboard = [
            [InlineKeyboardButton("✅ I Have Paid", callback_data="paid")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"👉 [Click here to pay]({payment_url})\n\n"
                f"After payment, click the button below to confirm."
            ),
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    else:
        error_message = data.get("message", "Unknown error")
        await context.bot.send_message(chat_id=chat_id, text="❌ Failed to create payment. Please try again later.")
        logging.error(f"Payment creation error: {data} — {error_message}")




async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    payment_id = user_invoices.get(user_id)

    if not payment_id:
        await query.edit_message_text("❌ No payment found for your account.")
        return

    headers = {"x-api-key": NOWPAY_API_KEY}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.nowpayments.io/v1/payment/{payment_id}",
            headers=headers
        )

    if response.status_code != 200:
        await query.edit_message_text("❌ Error checking payment. Try again later.")
        return

    data = response.json()
    status = data.get("payment_status")

    if status == "finished":
        await query.edit_message_text("✅ Payment confirmed! You've been upgraded.")
        # TODO: Mark user as paid in DB or upgrade their status
    elif status == "waiting":
        await query.edit_message_text("⏳ Payment still pending. Please wait a few minutes and try again.")
    else:
        await query.edit_message_text(f"❌ Payment status: {status}")


# ─── SIMULATOR ─────────────────────────────────────────────────────────────
async def bruteforce_simulator(user_id, context, chat_id):
    total_wallets_checked[user_id] = total_wallets_checked.get(user_id, 0)
    user_daily_checks[user_id] = user_daily_checks.get(user_id, 0)  # ensure initialized
    count, found_after = 0, random.randint(20, 50)
    stop_btn = InlineKeyboardButton("🛑 Stop", callback_data='stop')
    msg = await context.bot.send_message(chat_id=chat_id, text="🔍 Bruteforce running...", reply_markup=InlineKeyboardMarkup([[stop_btn]]))
    speed = 0.02 if is_pro(user_id) else 0.015
    networks = list(API_CONFIG.keys()) if is_pro(user_id) else [context.user_data.get('network')]
    try:
        while user_id in user_tasks:
            # 🔵 Add daily check limit check for free users
            if not is_pro(user_id):
                user_daily_checks[user_id] += 1
                if user_daily_checks[user_id] > 500:
                    await context.bot.send_message(chat_id=chat_id, text="🚫 You have reached your daily limit of 500 checks.\n\n🔓 Upgrade to Pro for unlimited checking!")
                    user_tasks[user_id].cancel()
                    del user_tasks[user_id]
                    break

            count += 1
            total_wallets_checked[user_id] += 1
            phrase = generate_real_mnemonic()
            address = generate_fake_address()
            net_status = []
            for network in networks:
                cfg = API_CONFIG[network]
                try:
                    bal = int(requests.get(cfg['url'], params={
                        "module": "account", "action": "balance", "address": address, "tag": "latest", "apikey": cfg['key']
                    }).json().get('result', 0)) / 1e18
                except:
                    bal = 0
                net_status.append(f"{network}:{bal:.6f}")
            status = " | ".join(net_status)

            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg.message_id,
                text=f"🔎 #{count}\nSeed: `{phrase}`\n{status}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[stop_btn]])
            )

            if count == found_after and any(float(s.split(':')[1]) > 0 for s in net_status):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"💥 *WALLET FOUND!*\n`{phrase}`\n{address}\n{status}",
                    parse_mode='Markdown'
                )
                found_after += random.randint(20, 50)

            await asyncio.sleep(speed)

    except asyncio.CancelledError:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)  # delete pink box
        except:
            pass
        if 'bruteforce_query' in context.user_data:
            try:
                await context.user_data['bruteforce_query'].answer("🛑 Bruteforce stopped.")  # pop-up
            except:
                pass




# ─── STATUS MESSAGE ────────────────────────────────────────────────────────
def format_user_status(user, user_id):
    now = datetime.now(timezone.utc)
    name = user.full_name
    username = f"@{user.username}" if user.username else "N/A"
    total = total_wallets_checked.get(user_id, 0)
    referred = user_referred_count.get(user_id, 0)

    if is_pro(user_id):
        expiry = datetime.fromtimestamp(pro_expiry[user_id], timezone.utc)
        days_left = (expiry - now).days
        sub_text = f"✅ Pro User (Expires in {days_left} days — {expiry.strftime('%b %d, %Y')})"
        checks_limit = "Unlimited"
    else:
        sub_text = "❌ Free User"
        checks_limit = "500 per day"

    daily_time = user_daily_bonus_claim.get(user_id, 0)
    daily_status = "Ready to Claim" if time.time() >= daily_time else datetime.fromtimestamp(daily_time, timezone.utc).strftime('%b %d, %Y - %H:%M UTC')

    bonus_balance = user_bonus_balance.get(user_id, 0)
    withdrawal_text = f"${bonus_balance:.2f} {'(Withdrawable)' if is_pro(user_id) else '(Locked - Upgrade to Pro)'}"

    return (
        f"📊 𝙐𝙨𝙚𝙧 𝘿𝙖𝙨𝙝𝙗𝙤𝙖𝙧𝙙\n\n"
        f"👤 Name: {name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 User ID: {user_id}\n\n"
        f"💼 Subscription Status: {sub_text}\n"
        f"🔒 Checks per Day Limit: {checks_limit}\n\n"
        f"🧮 Wallets Checked: {total}\n"
        f"🎯 Referrals Made: {referred}\n"
        f"💰 Bonus Balance: ${bonus_balance:.2f}\n"
        f"🎁 Next Daily Bonus Claim: {daily_status}\n\n"
        f"⏳ Current Server Time: {now.strftime('%b %d, %Y - %H:%M UTC')}\n\n"
        f"📢 Tip: Upgrade to Pro for Unlimited Checks and Exclusive Features!"
    )

def save_stats():
    with open("db.json", "w") as f:
        json.dump({
            "user_last_seen": user_last_seen,
            "user_pro_status": user_pro_status,
            "pro_expiry": pro_expiry,
            "total_wallets_checked": total_wallets_checked
        }, f)


# ─── COMMANDS ───────────────────────────────────────────────────────────────
async def start(update, context):
    user = update.effective_user
    user_id = user.id  # 🔑 THIS LINE is what was missing
    user_last_seen[user_id] = time.time()
    args = context.args if hasattr(context, 'args') else []
    if args:
        try:
            referrer_id = int(args[0])
            if referrer_id != user_id and user_id not in user_referrer:
                user_referrer[user_id] = referrer_id
                user_referred_count[referrer_id] = user_referred_count.get(referrer_id, 0) + 1
                user_bonus_balance[referrer_id] = round(user_bonus_balance.get(referrer_id, 0) + 0.01, 2)

                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎯 New referral joined!\n\n💵 You've earned +$0.01!\n💰 Total Bonus: ${user_bonus_balance[referrer_id]:.2f}"
                )
        except Exception as e:
            logging.error(f"Referral error: {e}")



    keyboard = [
        [InlineKeyboardButton("🚀 Start Bruteforce", callback_data='start')],
        [InlineKeyboardButton("💎 Purchase Pro", callback_data='purchase')],
        [InlineKeyboardButton("📊 Status", callback_data='user_status')],
        [InlineKeyboardButton("🎯 Refer & Earn", callback_data='refer')],
        [InlineKeyboardButton("🎁 Daily Check-in", callback_data='daily_bonus')],
        [InlineKeyboardButton("❓ Help", callback_data='help')]
    ]

    if update.message:  # If user typed /start
        await update.message.reply_text("Choose an action:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:  # If user clicked a button
        await context.bot.send_message(chat_id=user_id, text="Choose an action:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    back_btn = InlineKeyboardButton("⬅️ Back", callback_data='back')

    if data == 'start':
        if not is_pro(user_id):
            nets = [[InlineKeyboardButton(n, callback_data=f'net_{n}')] for n in API_CONFIG]
            await query.edit_message_text("Select network:", reply_markup=InlineKeyboardMarkup(nets))
        else:
            await query.edit_message_text("Starting on all networks...")
            task = context.application.create_task(bruteforce_simulator(user_id, context, query.message.chat_id))

            user_tasks[user_id] = task
    

    elif data.startswith('net_'):
        net = data.split('_', 1)[1]
        context.user_data['network'] = net
        await query.answer(f"Running on {net}...")  # 🚀 show pop-up

        # Remove the selection message
        try:
            await query.edit_message_text(f"🚀 Starting bruteforce on {net}...")
        except:
            pass

        # Start bruteforce
        task = context.application.create_task(bruteforce_simulator(user_id, context, query.message.chat_id))
        user_tasks[user_id] = task



    elif data == 'stop':
        if user_id in user_tasks:
            user_tasks[user_id].cancel()
            del user_tasks[user_id]
        await start(update, context)

    elif data == 'purchase':
        coins = ["LTC", "DOGE", "TRX", "BCH", "ETH", "BNB", "XRP", "DASH", "ZEC"]
        buttons = [[InlineKeyboardButton(f"Pay with {coin}", callback_data=f'pay_{coin}')] for coin in coins]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data='back')])
        await query.edit_message_text("Choose a payment method:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith('pay_'):
        coin = data.split('_', 1)[1]
        await create_payment(update, context, user_id, coin)

    elif data == 'user_status':
        msg = format_user_status(query.from_user, user_id)
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[back_btn]]))

    elif data == 'help':
        await query.edit_message_text("❓ For support, contact @supporthandle.", reply_markup=InlineKeyboardMarkup([[back_btn]]))

    elif data == 'refer':
        referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
        await query.edit_message_text(
            f"🎯 Share your referral link:\n\n🔗 {referral_link}\n\n✅ +50 free checks per referral!",
            reply_markup=InlineKeyboardMarkup([[back_btn]])
        )

    elif data == 'daily_bonus':
        next_time = user_daily_bonus_claim.get(user_id, 0)
        now = time.time()
        if now < next_time:
            wait = str(timedelta(seconds=int(next_time - now)))
            await query.edit_message_text(
                f"⏳ You can claim your next daily bonus in {wait}.",
                reply_markup=InlineKeyboardMarkup([[back_btn]])
            )
        else:
            user_daily_bonus_claim[user_id] = now + 24 * 3600
            user_bonus_balance[user_id] = round(user_bonus_balance.get(user_id, 0) + 0.05, 2)

            buttons = [[back_btn]]
            if user_bonus_balance[user_id] >= 1.00 and not user_withdraw_requests.get(user_id):
                buttons.insert(0, [InlineKeyboardButton("💵 Withdraw", callback_data='withdraw_request')])

            await query.edit_message_text(
                f"🎁 Bonus claimed! +$0.05 added.\n\n💰 Current Balance: ${user_bonus_balance[user_id]:.2f}",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

    elif data == 'withdraw_request':
        if user_bonus_balance.get(user_id, 0) < 1.00:
            await query.answer("❌ You need at least $1.00 to withdraw.", show_alert=True)
            return

        if user_withdraw_requests.get(user_id):
            await query.answer("🕓 You already submitted a withdrawal request.", show_alert=True)
            return

        user_withdraw_requests[user_id] = True
        await query.edit_message_text(
            "✅ Withdrawal request submitted!\n\n💵 Please send your Binance Pay ID or email to the admin for processing."
        )

        admin_id = 841443066  # Your admin Telegram chat ID
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"📥 New withdrawal request!\n\n👤 User: [{query.from_user.full_name}](tg://user?id={user_id})\n🆔 ID: {user_id}\n💰 Balance: ${user_bonus_balance[user_id]:.2f}\n\nPlease collect Binance details and process the payment.",
            parse_mode="Markdown"
        )

    elif data == "paid":
        invoice_id = user_invoices.get(user_id)

        # Fallback: try to recover invoice via user_orders
        if not invoice_id:
            for inv_id, uid in user_orders.items():
                if uid == user_id:
                    invoice_id = inv_id
                    break

        if not invoice_id:
            await query.edit_message_text("❌ No invoice found for your account. Please initiate the payment again.")
            return

        await query.edit_message_text("⏳ Checking your payment. Please wait up to 5 minutes...")

        headers = {"x-api-key": NOWPAY_API_KEY}
        check_interval = 10  # seconds
        max_wait_time = 5 * 60  # 5 minutes
        waited = 0

        while waited < max_wait_time:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"https://api.nowpayments.io/v1/payment/{invoice_id}",
                        headers=headers
                    )
                    if response.status_code != 200:
                        logging.warning(f"Status check failed ({response.status_code})")
                        await asyncio.sleep(check_interval)
                        waited += check_interval
                        continue

                    data = response.json()
                    status = data.get("payment_status")

                    if status == "finished":
                        user_pro_status[user_id] = True
                        pro_expiry[user_id] = time.time() + 30 * 24 * 3600
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="✅ Payment confirmed! You are now Pro for 30 days."
                        )
                        save_stats()
                        return
                    elif status == "waiting":
                        logging.info(f"Payment pending for user {user_id}...")

            except Exception as e:
                logging.error(f"Payment check failed: {e}")

            await asyncio.sleep(check_interval)
            waited += check_interval

        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Payment not confirmed in time. Please try again later."
        )




    elif data == 'back':
        try:
            await query.message.delete()  # 🧹 Delete the current message
        except:
            pass  # (in case message already deleted or can't be deleted)

        await start(update, context)  # Show main menu

def save_stats():
    with open("db.json", "w") as f:
        json.dump({
            "user_invoices": user_invoices,
            "user_orders": user_orders,
            "user_pro_status": user_pro_status,
            "pro_expiry": pro_expiry,
            "total_wallets_checked": total_wallets_checked
        }, f)

# ─── MAIN ───────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CallbackQueryHandler(check_payment, pattern="^paid$"))
    app.run_polling()


if __name__ == '__main__':
    from telegram.ext import CallbackQueryHandler

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling()


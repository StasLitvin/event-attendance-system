import asyncio
import threading
import os
from datetime import datetime, date, time
import secrets
from io import BytesIO

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from flask import Flask, render_template, request, jsonify, send_file
from io import BytesIO
import qrcode

from database import (
    SessionLocal, User, Event, Participant, Attendance,
    init_db, create_test_data
)

import qrcode

BOT_TOKEN = ''
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

print("Конфигурация:")
print(f"   Бот токен: {'Установлен' if BOT_TOKEN != 'YOUR_BOT_TOKEN_HERE' else 'НЕ УСТАНОВЛЕН'}")
print(f"   Веб-сервер: http://{WEB_HOST}:{WEB_PORT}")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

class ScanStates(StatesGroup):
    waiting_for_event = State()
    waiting_for_qr = State()

def get_user_from_db(telegram_id: int) -> User:
    db = SessionLocal()
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    db.close()
    return user

def check_access(telegram_id: int) -> bool:
    user = get_user_from_db(telegram_id)
    return user and user.is_active

@router.message(Command("start"))
async def cmd_start(message: Message):
    user = get_user_from_db(message.from_user.id)

    if not user:
        await message.answer(
            "Добро пожаловать!\n\n"
            "Вы не зарегистрированы в системе.\n"
            "Обратитесь к администратору.\n\n"
            f"Ваш Telegram ID: `{message.from_user.id}`",
            parse_mode="Markdown"
        )
        return

    role_emoji = "‍" if user.role == "admin" else ""
    role_text = "Администратор" if user.role == "admin" else "Организатор"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сканировать QR-код", callback_data="scan_qr")],
        [InlineKeyboardButton(text="Статистика", callback_data="my_stats")],
    ])

    await message.answer(
        f"*Система учета посещений*\n\n"
        f"{role_emoji} Роль: {role_text}\n"
        f"Имя: {user.full_name or 'Не указано'}\n\n"
        f"Выберите действие:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "scan_qr")
async def start_scan(callback: CallbackQuery, state: FSMContext):
    if not check_access(callback.from_user.id):
        await callback.answer("У вас нет доступа", show_alert=True)
        return

    db = SessionLocal()
    today = date.today()
    events = db.query(Event).filter(
        Event.is_active == True,
        Event.event_date >= datetime.combine(today, datetime.min.time())
    ).order_by(Event.event_date, Event.start_time).limit(10).all()
    db.close()

    if not events:
        await callback.message.answer("Нет активных событий")
        await callback.answer()
        return

    keyboard = []
    for event in events:
        date_str = event.event_date.strftime("%d.%m.%Y")
        time_str = event.start_time.strftime("%H:%M") if event.start_time else ""
        button_text = f"{event.title} | {date_str} {time_str}"
        keyboard.append([InlineKeyboardButton(
            text=button_text[:64],
            callback_data=f"event_{event.id}"
        )])

    keyboard.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])

    await callback.message.answer(
        "*Выберите событие:*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )
    await state.set_state(ScanStates.waiting_for_event)
    await callback.answer()

@router.callback_query(F.data.startswith("event_"), StateFilter(ScanStates.waiting_for_event))
async def event_selected(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split("_")[1])
    await state.update_data(event_id=event_id)

    db = SessionLocal()
    event = db.query(Event).filter(Event.id == event_id).first()
    db.close()

    if not event:
        await callback.message.answer("Событие не найдено")
        await state.clear()
        await callback.answer()
        return

    await callback.message.answer(
        f"*Выбрано:* {event.title}\n\n"
        f"Отправьте текст QR-кода участника:",
        parse_mode="Markdown"
    )
    await state.set_state(ScanStates.waiting_for_qr)
    await callback.answer()

@router.message(StateFilter(ScanStates.waiting_for_qr), F.text)
async def process_qr_text(message: Message, state: FSMContext):
    qr_code = message.text.strip()
    data = await state.get_data()
    event_id = data.get("event_id")

    db = SessionLocal()

    participant = db.query(Participant).filter(
        Participant.qr_code == qr_code,
        Participant.event_id == event_id
    ).first()

    if not participant:
        db.close()
        await message.answer("*Участник не найден*\n\nПроверьте QR-код", parse_mode="Markdown")
        return

    existing = db.query(Attendance).filter(
        Attendance.event_id == event_id,
        Attendance.participant_id == participant.id
    ).first()

    if existing:
        scan_time = existing.scanned_at.strftime("%d.%m.%Y %H:%M")
        db.close()
        await message.answer(
            f"*Участник уже отмечен!*\n\n"
            f"{participant.full_name}\n"
            f"{scan_time}",
            parse_mode="Markdown"
        )
        return

    user = db.query(User).filter(User.telegram_id == message.from_user.id).first()

    attendance = Attendance(
        event_id=event_id,
        participant_id=participant.id,
        scanned_by=user.id
    )
    db.add(attendance)
    db.commit()

    total = db.query(Participant).filter(Participant.event_id == event_id).count()
    attended = db.query(Attendance).filter(Attendance.event_id == event_id).count()

    db.close()

    await message.answer(
        f"*Посещение зарегистрировано!*\n\n"
        f"{participant.full_name}\n"
        f"{participant.email or 'Не указан'}\n"
        f"{datetime.now().strftime('%H:%M:%S')}\n\n"
        f"Пришло: *{attended}/{total}* участников",
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "my_stats")
async def my_stats(callback: CallbackQuery):
    db = SessionLocal()

    user = db.query(User).filter(User.telegram_id == callback.from_user.id).first()

    scans_today = db.query(Attendance).filter(
        Attendance.scanned_by == user.id,
        Attendance.scanned_at >= datetime.combine(date.today(), datetime.min.time())
    ).count()

    scans_total = db.query(Attendance).filter(Attendance.scanned_by == user.id).count()

    active_events = db.query(Event).filter(Event.is_active == True).count()

    db.close()

    await callback.message.answer(
        f"*Ваша статистика*\n\n"
        f"Активных событий: {active_events}\n"
        f"Сканирований сегодня: {scans_today}\n"
        f"Всего сканирований: {scans_total}",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Операция отменена")
    await callback.answer()

app = Flask(__name__, template_folder='templates')

@app.route("/")
def dashboard():
    db = SessionLocal()

    total_events = db.query(Event).count()
    active_events = db.query(Event).filter(Event.is_active == True).count()
    total_participants = db.query(Participant).count()
    total_attendances = db.query(Attendance).count()

    recent_events = db.query(Event).order_by(Event.created_at.desc()).limit(5).all()

    db.close()

    return render_template("dashboard.html",
                           total_events=total_events,
                           active_events=active_events,
                           total_participants=total_participants,
                           total_attendances=total_attendances,
                           recent_events=recent_events
                           )

@app.route("/api/events/create", methods=["POST"])
def create_event():
    title = request.form.get("title")
    description = request.form.get("description", "")
    event_date = request.form.get("event_date")
    start_time = request.form.get("start_time", "")
    end_time = request.form.get("end_time", "")
    location = request.form.get("location", "")
    max_participants = int(request.form.get("max_participants", 0))
    admin_telegram_id = int(request.form.get("admin_telegram_id"))

    db = SessionLocal()

    admin = db.query(User).filter(
        User.telegram_id == admin_telegram_id,
        User.role == "admin"
    ).first()

    if not admin:
        db.close()
        return jsonify({"error": "Недостаточно прав"}), 403

    from datetime import datetime, time as dt_time

    event_datetime = datetime.strptime(event_date, "%Y-%m-%d")
    start_t = dt_time.fromisoformat(start_time) if start_time else None
    end_t = dt_time.fromisoformat(end_time) if end_time else None

    event = Event(
        title=title,
        description=description,
        event_date=event_datetime,
        start_time=start_t,
        end_time=end_t,
        location=location,
        max_participants=max_participants if max_participants > 0 else None,
        created_by=admin.id
    )

    db.add(event)
    db.commit()
    db.refresh(event)
    db.close()

    return jsonify({"success": True, "event_id": event.id})

@app.route("/api/participants/register", methods=["POST"])
def register_participant():
    event_id = int(request.form.get("event_id"))
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    phone = request.form.get("phone")

    db = SessionLocal()

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        db.close()
        return jsonify({"error": "Событие не найдено"}), 404

    import secrets
    qr_code = f"EVT{event_id}_{secrets.token_urlsafe(8)}"

    participant = Participant(
        event_id=event_id,
        full_name=full_name,
        email=email,
        phone=phone,
        qr_code=qr_code
    )

    db.add(participant)
    db.commit()
    db.refresh(participant)
    db.close()

    return jsonify({
        "success": True,
        "participant_id": participant.id,
        "qr_code": qr_code,
        "qr_url": f"/api/qr/{qr_code}"
    })

@app.route("/api/qr/<qr_code>")
def generate_qr(qr_code):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_code)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(buf, mimetype="image/png")

@app.route("/api/events")
def get_events():
    db = SessionLocal()
    events = db.query(Event).filter(Event.is_active == True).order_by(Event.event_date.desc()).all()

    result = []
    for event in events:
        total = db.query(Participant).filter(Participant.event_id == event.id).count()
        attended = db.query(Attendance).filter(Attendance.event_id == event.id).count()

        result.append({
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "event_date": event.event_date.isoformat(),
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "location": event.location,
            "total_participants": total,
            "attended": attended
        })

    db.close()
    return jsonify(result)

def start_web():
    """Запуск Flask веб-сервера"""
    print(f"Запуск Flask веб-сервера на http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)

async def start_bot():
    """Запуск Telegram бота"""
    dp.include_router(router)
    print("Запуск Telegram бота...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

def start_web():
    """Запуск веб-сервера"""
    print(f"Запуск веб-сервера на http://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")

async def main():
    """Главная функция запуска"""
    print("\n" + "=" * 50)
    print("EVENT ATTENDANCE SYSTEM")
    print("=" * 50 + "\n")

    if not init_db():
        print("Не удалось подключиться к БД. Проверьте настройки PostgreSQL")
        return

    create_test_data()

    print("\nЗапуск сервисов...\n")

    web_thread = threading.Thread(target=start_web, daemon=True)
    web_thread.start()

    await asyncio.sleep(2)

    try:
        await start_bot()
    except KeyboardInterrupt:
        print("\n\n Остановка сервисов...")
    except Exception as e:
        print(f"\nОшибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())

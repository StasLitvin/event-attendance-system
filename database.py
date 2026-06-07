from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean, Time, Text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://root:PASSWORD@localhost:5432/newdb?client_encoding=utf8"
)

Base = declarative_base()

class User(Base):
    """Пользователи системы (организаторы и администраторы)"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(255))
    full_name = Column(String(255))
    role = Column(String(20), default="organizer")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    scanned_attendances = relationship("Attendance", back_populates="scanned_by_user")

class Event(Base):
    """События/мероприятия"""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    event_date = Column(DateTime, nullable=False, index=True)
    start_time = Column(Time)
    end_time = Column(Time)
    location = Column(String(500))
    max_participants = Column(Integer)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))

    attendances = relationship("Attendance", back_populates="event", cascade="all, delete-orphan")
    participants = relationship("Participant", back_populates="event", cascade="all, delete-orphan")

class Participant(Base):
    """Участники событий"""
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255))
    phone = Column(String(50))
    qr_code = Column(String(255), unique=True, nullable=False, index=True)
    registered_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("Event", back_populates="participants")
    attendances = relationship("Attendance", back_populates="participant")

class Attendance(Base):
    """Отметки о посещении"""
    __tablename__ = "attendances"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True)
    scanned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    scanned_at = Column(DateTime, default=datetime.utcnow, index=True)

    event = relationship("Event", back_populates="attendances")
    participant = relationship("Participant", back_populates="attendances")
    scanned_by_user = relationship("User", back_populates="scanned_attendances")

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Создание всех таблиц"""
    try:
        Base.metadata.create_all(bind=engine)
        print("База данных PostgreSQL инициализирована")
        print(f"Таблицы: {', '.join(Base.metadata.tables.keys())}")
        return True
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")
        return False

def get_db():
    """Получение сессии БД"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_test_data():
    """Создание тестовых данных"""
    db = SessionLocal()
    try:

        if db.query(User).first():
            print(" Тестовые данные уже существуют")
            return

        admin = User(
            telegram_id=123456789,
            username="admin",
            full_name="Администратор",
            role="admin"
        )
        db.add(admin)

        organizer = User(
            telegram_id=987654321,
            username="organizer",
            full_name="Организатор",
            role="organizer"
        )
        db.add(organizer)
        db.commit()

        print("Тестовые пользователи созданы:")
        print(f"   ‍Админ: telegram_id=123456789")
        print(f"   Организатор: telegram_id=987654321")

    except Exception as e:
        print(f"Ошибка создания тестовых данных: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
    create_test_data()

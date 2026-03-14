import mysql.connector
from mysql.connector import Error


def create_connection():
    """Connect to MySQL database."""
    try:
        connection = mysql.connector.connect(
            host='localhost',
            user='root',
            password='root123',
            database='creovibe_db',
            auth_plugin='mysql_native_password',
            charset='utf8mb4',
            use_unicode=True
        )
        if connection.is_connected():
            return connection
        return None
    except Error as e:
        print(f"Database connection error: {e}")
        return None


def init_db():
    """Initialize core tables using the current schema."""
    connection = create_connection()
    if not connection:
        print("Cannot initialize schema without database connection")
        return

    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS artist_table (
                Artist_ID INT AUTO_INCREMENT PRIMARY KEY,
                First_Name VARCHAR(100) NOT NULL,
                Last_Name VARCHAR(100) NOT NULL,
                Username VARCHAR(100) NOT NULL UNIQUE,
                Password VARCHAR(255) NOT NULL,
                Email VARCHAR(255),
                Gender VARCHAR(20),
                dob DATE,
                Phone_Number VARCHAR(20),
                State_ID INT,
                City_ID INT,
                category_id INT,
                Portfolio_Path TEXT,
                profile_pic TEXT,
                experience_years INT DEFAULT 0,
                price_per_hour DECIMAL(10,2) DEFAULT 0.00,
                rating DECIMAL(3,2) DEFAULT 0.00,
                verification_status VARCHAR(20) DEFAULT 'pending',
                is_enabled TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS client_table (
                Client_ID INT AUTO_INCREMENT PRIMARY KEY,
                First_Name VARCHAR(100) NOT NULL,
                Last_Name VARCHAR(100) NOT NULL,
                Username VARCHAR(100) NOT NULL UNIQUE,
                Password VARCHAR(255) NOT NULL,
                Gender VARCHAR(20),
                Dob DATE,
                Phone_Number VARCHAR(20),
                Pincode VARCHAR(10),
                State_ID INT,
                city_id INT,
                Is_Enabled TINYINT(1) DEFAULT 1,
                Is_Admin TINYINT(1) DEFAULT 0,
                Created_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_table (
                Slot_ID INT AUTO_INCREMENT PRIMARY KEY,
                Artist_ID INT NOT NULL,
                Slot_Date DATE NOT NULL,
                Start_Time TIME NOT NULL,
                End_Time TIME NOT NULL,
                Status ENUM('Available','Blocked') NOT NULL DEFAULT 'Available',
                Slot_Type ENUM('Communication','Performance') NOT NULL,
                Description TEXT,
                Created_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                Updated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_calendar_artist FOREIGN KEY (Artist_ID) REFERENCES artist_table (Artist_ID)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_table (
                Booking_ID INT AUTO_INCREMENT PRIMARY KEY,
                Client_ID INT NOT NULL,
                Artist_ID INT NOT NULL,
                Slot_ID INT NOT NULL,
                Booking_Status VARCHAR(50) NOT NULL,
                Booked_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_booking_client FOREIGN KEY (Client_ID) REFERENCES client_table (Client_ID),
                CONSTRAINT fk_booking_artist FOREIGN KEY (Artist_ID) REFERENCES artist_table (Artist_ID),
                CONSTRAINT fk_booking_slot FOREIGN KEY (Slot_ID) REFERENCES calendar_table (Slot_ID)
            )
            """
        )

        connection.commit()
        print("Schema initialized successfully")
    except Error as e:
        print(f"Schema initialization error: {e}")
    finally:
        cursor.close()
        connection.close()


def update_schema():
    """Backwards compatibility alias."""
    init_db()


if __name__ == "__main__":
    init_db()

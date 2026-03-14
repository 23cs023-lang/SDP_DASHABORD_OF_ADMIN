from database import create_connection


def seed_artist_data():
    connection = create_connection()
    if not connection:
        print("Database connection failed")
        return

    cursor = connection.cursor()
    try:
        artists = [
            (
                'Priya', 'Patel', 'priya_pat', 'default123', 'priya@example.com',
                'Female', '1995-06-12', '9876543210', 1, 1, 1, '',
                '', 5, 5000, 4.5, 'approved', 1
            ),
            (
                'Ravi', 'Shah', 'ravi_shah', 'default123', 'ravi@example.com',
                'Male', '1992-11-05', '9876543211', 1, 1, 2, '',
                '', 7, 7000, 4.7, 'pending', 1
            )
        ]

        cursor.executemany(
            """
            INSERT INTO artist_table
                (First_Name, Last_Name, Username, Password, Email, Gender, dob, Phone_Number,
                 State_ID, City_ID, category_id, Portfolio_Path, profile_pic, experience_years,
                 price_per_hour, rating, verification_status, is_enabled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            artists
        )
        connection.commit()
        print(f"Inserted {cursor.rowcount} artists")
    except Exception as e:
        print(f"Seed failed: {e}")
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    seed_artist_data()

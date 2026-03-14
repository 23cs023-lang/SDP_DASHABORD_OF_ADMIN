print("Testing which database.py is imported...")
import database

print(f"Database module location: {database.__file__}")

# Test the function
print("\nTesting create_connection()...")
conn = database.create_connection()
if conn:
    print("✅ Real database.py is being used!")
    conn.close()
else:
    print("❌ Fake/demo database.py is being used!")
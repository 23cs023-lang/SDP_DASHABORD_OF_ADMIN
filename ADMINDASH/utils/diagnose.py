print("=" * 50)
print("DIAGNOSING THE ISSUE")
print("=" * 50)

# Test 1: Can we import mysql.connector?
try:
    import mysql.connector
    print("✅ 1. mysql.connector is installed")
except ImportError:
    print("❌ 1. mysql.connector NOT installed")
    print("   Run: pip install mysql-connector-python")
    exit()

# Test 2: Test connection WITHOUT database
print("\n🔍 2. Testing connection to MySQL server...")
try:
    conn = mysql.connector.connect(
        host='localhost',
        user='root',
        password='root123',
        auth_plugin='mysql_native_password'
    )
    
    if conn.is_connected():
        print("   ✅ Connected to MySQL server!")
        
        # Check databases
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES")
        dbs = [db[0] for db in cursor.fetchall()]
        
        print(f"   📊 Found {len(dbs)} databases")
        
        # Check if our database exists
        if 'creovibe_db' in dbs:
            print("   ✅ 'creovibe_db' database exists")
        else:
            print("   ❌ 'creovibe_db' database DOES NOT exist")
            print("\n   To create it, run in MySQL:")
            print("   CREATE DATABASE creovibe_db;")
            print("   USE creovibe_db;")
            
        cursor.close()
        conn.close()
    else:
        print("   ❌ Connection failed")
        
except mysql.connector.Error as err:
    print(f"   ❌ Connection error: {err}")
    print("\n   Possible fixes:")
    print("   1. Check if password 'root123' is correct")
    print("   2. Try without password if you haven't set one")
    print("   3. Try: mysql -u root -p (in command prompt)")

# Test 3: Try connecting WITH database
print("\n🔍 3. Testing connection to 'creovibe_db'...")
try:
    conn2 = mysql.connector.connect(
        host='localhost',
        user='root',
        password='root123',
        database='creovibe_db',
        auth_plugin='mysql_native_password'
    )
    
    if conn2.is_connected():
        print("   ✅ Connected to 'creovibe_db' database!")
        
        # Check tables
        cursor = conn2.cursor()
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        if tables:
            print(f"   📋 Found {len(tables)} tables:")
            for table in tables:
                print(f"     - {table[0]}")
        else:
            print("   ℹ️  No tables found")
            
        cursor.close()
        conn2.close()
        
except mysql.connector.Error as err:
    print(f"   ❌ Error: {err}")

print("\n" + "=" * 50)
print("RUN THIS SCRIPT AND TELL ME WHAT YOU SEE")
print("=" * 50)
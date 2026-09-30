import gspread
from datetime import datetime

# 1. Autentikasi menggunakan file JSON yang tadi diunduh dan diubah namanya
gc = gspread.service_account(filename='cow-monitoring-cv-53cdf9db3ca0.json')

# 2. Buka spreadsheet berdasarkan Judul dokumen
# Ganti teks di bawah dengan Judul file Google Sheets Anda
sh = gc.open("Cow-Monitoring-CV")

# Ambil sheet pertama (Sheet1)
worksheet = sh.sheet1 

# 3. Siapkan data percobaan (Mirip format tabel aktivitas sapi)
waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
data_baru = [waktu_sekarang, "Sapi_01", "Makan", "Durasi 10 menit"]

# 4. Tambahkan data ke baris paling bawah secara otomatis
worksheet.append_row(data_baru)

print("Berhasil! Coba cek file Google Sheets Anda sekarang.")
# Menghubungkan router AI

Adapter assessment, analisis referensi dan tes koneksi sudah memakai router sungguhan ketika AI_MODE=openai. Nama mode ini mengikuti contract dan menyatakan protokol kompatibel OpenAI; modelnya dapat berasal dari Gemini atau provider lain.

## Konfigurasi

Isi .env di folder backend. Contoh endpoint OpenRouter:

```env
AI_MODE=openai
AI_BASE_URL=https://openrouter.ai/api/v1
AI_API_KEY=
AI_MODEL=
AI_PROVIDER_NAME=openrouter
AI_TIMEOUT_SECONDS=60
AI_MAX_OUTPUT_TOKENS=6000
AI_JSON_MODE=true
AI_IMAGE_ANALYSIS_ENABLED=false
```

AI_MODEL harus sama persis dengan ID model pada katalog router Anda. Isi key dari layanan router yang sama dengan AI_BASE_URL. Base URL harus mencakup prefix API, tanpa /chat/completions di akhir. Endpoint router lain dapat digunakan jika mendukung protokol yang sama.

Untuk analisis gambar, pilih model yang menerima image input dan ubah AI_IMAGE_ANALYSIS_ENABLED=true. Flag ini adalah deklarasi konfigurasi, bukan hasil pemeriksaan kemampuan model. Analisis referensi memerlukan file unggahan lokal; public URL tidak diunduh. includeImages=true pada assessment mengirim gambar unggahan/demo yang tersedia, jika vision diaktifkan. Gambar dikecilkan maksimal 1024×1024 dan dikirim sebagai JPEG base64. Jumlah gambar nyata dicatat di imagesSent. Versi hanya metadata tidak mengirim gambar. File input gambar assessment disimpan sebagai snapshot base64 untuk retry yang konsisten; ekspor tidak memuat snapshot biner itu.

## Jalankan dan tes

Dari folder backend:

```powershell
docker compose up -d --build
Invoke-RestMethod -Uri http://localhost:8000/api/v1/settings/ai
```

Pastikan mode=openai dan providerConfigured=true. providerConfigured hanya menunjukkan adanya key; belum membuktikan key valid.

Tes koneksi berikut membuat satu panggilan ke model dan dapat dikenakan biaya oleh router:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/settings/ai/test
```

Untuk assessment gunakan mode AI_ASSISTED atau HYBRID, bukan RULES_ONLY. Jika target direction sudah diaktifkan, kirim oldDirectionRevisionId yang sebelumnya secara eksplisit. Pengujian dan contoh request tersedia di http://localhost:8000/docs.

AI_ASSISTED menggunakan klasifikasi AI. HYBRID mempertahankan klasifikasi dan urutan rules, lalu AI memperkaya alasan, evidence dan tindakan; peringatan tambahan tetap meminta review. RULES_ONLY dan AI_MODE=mock tidak memanggil router. Keputusan AI tidak pernah otomatis mengganti versi, approval atau collection pin.

## Bila gagal

- AI_NOT_CONFIGURED: periksa mode, endpoint, key/model atau flag vision.
- PROVIDER_TIMEOUT: periksa layanan router; timeout dapat diatur hingga 300 detik.
- PROVIDER_ERROR: key/model tidak diterima, model tidak mendukung gambar/JSON mode, atau respons tidak memenuhi schema. Jika router/model tidak mendukung response_format=json_object, coba AI_JSON_MODE=false; output tetap harus JSON valid.
- Respons terpotong: kurangi jumlah aset atau naikkan AI_MAX_OUTPUT_TOKENS. Batas output token berlaku untuk satu request, bukan jaminan biaya maksimum.
- Assessment gagal disimpan sebagai FAILED dan dapat di-retry dengan snapshot yang sama. Tidak ada retry otomatis atau fallback diam-diam ke mock.

Key tidak dikembalikan oleh API. Budget bulanan belum dihitung/ditegakkan oleh backend; gunakan kontrol biaya dari router. Analisis masih berjalan sinkron. Protokol provider native yang berbeda membutuhkan adapter terpisah.

## Rujukan protokol

- [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
- [OpenRouter Image Inputs](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding)

"use strict";

/* ==============================
   عناصر الصفحة
============================== */

const videoInput = document.getElementById("videoInput");
const videoPreview = document.getElementById("videoPreview");
const startButton = document.getElementById("startButton");

const sourceLanguage = document.getElementById("sourceLanguage");
const targetLanguage = document.getElementById("targetLanguage");
const subtitles = document.getElementById("subtitles");

const progressBox = document.getElementById("progressBox");
const progressBar = document.getElementById("progressBar");
const progressPercent = document.getElementById("progressPercent");
const statusText = document.getElementById("statusText");

const downloadButton = document.getElementById("downloadButton");
const errorText = document.getElementById("errorText");


/* ==============================
   متغيرات التطبيق
============================== */

let selectedVideo = null;
let jobId = null;
let videoObjectUrl = null;
let statusTimer = null;


/* ==============================
   التحقق من عناصر الصفحة
============================== */

if (
    !videoInput ||
    !videoPreview ||
    !startButton ||
    !sourceLanguage ||
    !targetLanguage ||
    !subtitles ||
    !progressBox ||
    !progressBar ||
    !progressPercent ||
    !statusText ||
    !downloadButton ||
    !errorText
) {
    console.error(
        "DubAI: بعض عناصر index.html غير موجودة."
    );
}


/* ==============================
   اختيار الفيديو
============================== */

if (videoInput) {

    videoInput.addEventListener(
        "change",
        function () {

            clearError();

            hideDownload();

            selectedVideo = null;
            jobId = null;

            if (
                !this.files ||
                this.files.length === 0
            ) {
                startButton.disabled = true;

                if (videoPreview) {
                    videoPreview.style.display =
                        "none";

                    videoPreview.removeAttribute(
                        "src"
                    );

                    videoPreview.load();
                }

                return;
            }

            const file = this.files[0];


            /* التحقق من نوع الملف */

            if (
                !file.type ||
                !file.type.startsWith("video/")
            ) {
                showError(
                    "الملف المختار ليس فيديو."
                );

                this.value = "";

                startButton.disabled = true;

                return;
            }


            /* التحقق من حجم الفيديو */

            const maxSize =
                500 * 1024 * 1024;

            if (file.size > maxSize) {

                showError(
                    "حجم الفيديو أكبر من 500MB."
                );

                this.value = "";

                startButton.disabled = true;

                return;
            }


            selectedVideo = file;


            /* إنشاء رابط المعاينة */

            if (videoObjectUrl) {
                URL.revokeObjectURL(
                    videoObjectUrl
                );
            }

            videoObjectUrl =
                URL.createObjectURL(
                    selectedVideo
                );


            if (videoPreview) {

                videoPreview.src =
                    videoObjectUrl;

                videoPreview.style.display =
                    "block";

                videoPreview.load();
            }


            startButton.disabled = false;

            setProgress(
                0,
                "الفيديو جاهز للمعالجة."
            );
        }
    );
}


/* ==============================
   زر بدء الدبلجة
============================== */

if (startButton) {

    startButton.addEventListener(
        "click",
        async function () {

            if (!selectedVideo) {

                showError(
                    "اختر فيديو أولاً."
                );

                return;
            }


            /* تعطيل الزر أثناء العمل */

            startButton.disabled = true;

            clearError();

            hideDownload();

            progressBox.style.display =
                "block";


            setProgress(
                1,
                "جاري رفع الفيديو..."
            );


            try {

                /* ==========================
                   رفع الفيديو
                ========================== */

                const uploadData =
                    new FormData();

                uploadData.append(
                    "file",
                    selectedVideo
                );


                const uploadResponse =
                    await fetch(
                        "/api/upload",
                        {
                            method: "POST",
                            body: uploadData
                        }
                    );


                if (!uploadResponse.ok) {

                    const message =
                        await readError(
                            uploadResponse
                        );

                    throw new Error(
                        message
                    );
                }


                const uploadResult =
                    await uploadResponse.json();


                if (
                    !uploadResult ||
                    !uploadResult.job_id
                ) {

                    throw new Error(
                        "الخادم لم يُرجع رقم المهمة."
                    );
                }


                jobId =
                    uploadResult.job_id;


                setProgress(
                    3,
                    "تم رفع الفيديو. جاري بدء الدبلجة..."
                );


                /* ==========================
                   بدء الدبلجة والترجمة
                ========================== */

                const dubData =
                    new FormData();


                dubData.append(
                    "source",
                    sourceLanguage.value
                );


                dubData.append(
                    "target",
                    targetLanguage.value
                );


                dubData.append(
                    "subtitles",
                    subtitles.checked
                        ? "true"
                        : "false"
                );


                const dubResponse =
                    await fetch(
                        "/api/dub/" + jobId,
                        {
                            method: "POST",
                            body: dubData
                        }
                    );


                if (!dubResponse.ok) {

                    const message =
                        await readError(
                            dubResponse
                        );

                    throw new Error(
                        message
                    );
                }


                const dubResult =
                    await dubResponse.json();


                if (
                    !dubResult ||
                    dubResult.ok !== true
                ) {

                    throw new Error(
                        "تعذر بدء عملية الدبلجة."
                    );
                }


                /* ==========================
                   متابعة المعالجة
                ========================== */

                checkStatus();

            } catch (error) {

                console.error(
                    "DubAI error:",
                    error
                );

                showError(
                    getErrorMessage(error)
                );
            }
        }
    );
}


/* ==============================
   متابعة حالة الفيديو
============================== */

async function checkStatus() {

    if (!jobId) {
        return;
    }


    try {

        const response =
            await fetch(
                "/api/status/" + jobId,
                {
                    method: "GET",
                    cache: "no-store"
                }
            );


        if (!response.ok) {

            const message =
                await readError(
                    response
                );

            throw new Error(
                message
            );
        }


        const data =
            await response.json();


        if (!data) {

            throw new Error(
                "الخادم أرسل بيانات غير صحيحة."
            );
        }


        /* ==========================
           تحديث التقدم
        ========================== */

        const progress =
            Number(data.progress || 0);

        const message =
            data.message ||
            "جاري معالجة الفيديو...";


        setProgress(
            progress,
            message
        );


        /* ==========================
           انتهت العملية
        ========================== */

        if (
            data.status === "done"
        ) {

            setProgress(
                100,
                "✅ اكتملت الدبلجة والترجمة بنجاح!"
            );


            let downloadUrl =
                data.download;


            if (!downloadUrl) {

                downloadUrl =
                    "/api/download/" +
                    jobId;
            }


            downloadButton.href =
                downloadUrl;

            downloadButton.download =
                "DubAI_result.mp4";

            downloadButton.style.display =
                "block";


            startButton.disabled =
                false;


            stopStatusTimer();

            return;
        }


        /* ==========================
           حدث خطأ في الخادم
        ========================== */

        if (
            data.status === "error"
        ) {

            showError(
                data.error ||
                data.message ||
                "حدث خطأ أثناء معالجة الفيديو."
            );

            return;
        }


        /* ==========================
           ما زالت المعالجة مستمرة
        ========================== */

        stopStatusTimer();

        statusTimer =
            setTimeout(
                checkStatus,
                1500
            );

    } catch (error) {

        console.error(
            "Status error:",
            error
        );

        showError(
            getErrorMessage(error)
        );
    }
}


/* ==============================
   تحديث شريط التقدم
============================== */

function setProgress(
    percent,
    message
) {

    if (!progressBar) {
        return;
    }


    let value =
        Number(percent);


    if (!Number.isFinite(value)) {
        value = 0;
    }


    value =
        Math.max(
            0,
            Math.min(
                100,
                value
            )
        );


    progressBar.style.width =
        value + "%";


    if (progressPercent) {

        progressPercent.textContent =
            Math.round(value) + "%";
    }


    if (statusText) {

        statusText.textContent =
            message ||
            "جاري المعالجة...";
    }
}


/* ==============================
   عرض الخطأ
============================== */

function showError(message) {

    stopStatusTimer();


    if (errorText) {

        errorText.textContent =
            "❌ " +
            (
                message ||
                "حدث خطأ غير معروف."
            );
    }


    if (startButton) {

        startButton.disabled =
            false;
    }
}


/* ==============================
   مسح الخطأ
============================== */

function clearError() {

    if (errorText) {

        errorText.textContent =
            "";
    }
}


/* ==============================
   إخفاء زر التحميل
============================== */

function hideDownload() {

    if (downloadButton) {

        downloadButton.style.display =
            "none";

        downloadButton.removeAttribute(
            "href"
        );
    }
}


/* ==============================
   إيقاف المؤقت
============================== */

function stopStatusTimer() {

    if (statusTimer) {

        clearTimeout(
            statusTimer
        );

        statusTimer = null;
    }
}


/* ==============================
   قراءة رسالة الخطأ من الخادم
============================== */

async function readError(response) {

    try {

        const data =
            await response.json();


        if (
            data &&
            data.detail
        ) {

            if (
                typeof data.detail ===
                "string"
            ) {
                return data.detail;
            }

            return JSON.stringify(
                data.detail
            );
        }

    } catch (e) {
        /* تجاهل الخطأ */
    }


    try {

        const text =
            await response.text();

        if (text) {
            return text;
        }

    } catch (e) {
        /* تجاهل الخطأ */
    }


    return (
        "خطأ من الخادم. رمز الحالة: " +
        response.status
    );
}


/* ==============================
   استخراج رسالة الخطأ
============================== */

function getErrorMessage(error) {

    if (!error) {

        return "خطأ غير معروف.";
    }


    if (error.message) {

        return error.message;
    }


    return String(error);
}


/* ==============================
   تنظيف عند إغلاق الصفحة
============================== */

window.addEventListener(
    "beforeunload",
    function () {

        stopStatusTimer();


        if (videoObjectUrl) {

            URL.revokeObjectURL(
                videoObjectUrl
            );

            videoObjectUrl = null;
        }
    }
);

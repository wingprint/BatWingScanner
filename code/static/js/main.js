function showImage(src) {
    src = src.replace("small_", "").replace(".jpg", "")
    document.getElementById('fullImage').src = src;
}


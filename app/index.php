<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Docker Compose PHP Project</title>
    <link rel="stylesheet" href="assets/css/style.css">
</head>
<body>
    <div class="container">
        <h1>Hello from Docker!</h1>
        <p>
            <?php
            echo "PHP Version: " . phpversion();
            ?>
        </p>
        <button id="clickMe">Click Me</button>
        <p id="message"></p>
    </div>
    <script src="assets/js/script.js"></script>
</body>
</html>

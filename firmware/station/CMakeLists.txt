# ESP-IDF-Projekt der Aussenstation.
#
# Gebaut wird fuer den klassischen ESP32-WROOM-32. Der hat keinen in C
# programmierbaren ULP-Kern -- deshalb laeuft die Station im Dauerbetrieb mit
# automatischem Light-Sleep und zaehlt die Reed-Impulse per GPIO-Interrupt,
# statt im Tiefschlaf mit ULP-Assembler zu arbeiten.
#
#   . $HOME/esp/esp-idf/export.sh
#   idf.py set-target esp32
#   idf.py build
#   idf.py -p /dev/ttyUSB0 flash monitor

cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wetterstation)

# Nur die eigenen Quellen streng pruefen -- das SDK ist nicht warnungsfrei und
# ist nicht unsere Baustelle.
idf_build_set_property(COMPILE_OPTIONS "-Wall;-Wextra" APPEND)

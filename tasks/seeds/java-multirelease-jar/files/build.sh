#!/bin/sh
set -eu

if [ -n "${JAVA_HOME:-}" ]; then
    JAVAC="$JAVA_HOME/bin/javac"
else
    JAVAC=javac
fi

rm -rf build
mkdir -p build/classes/base build/classes/java11 build/libs

"$JAVAC" --release 8 \
    -d build/classes/base \
    src/main/java/com/acme/greeter/GreetingService.java \
    src/main/java/com/acme/greeter/RuntimeGreeting.java \
    src/main/java/com/acme/app/Main.java

"$JAVAC" --release 11 \
    -classpath build/classes/base \
    -d build/classes/java11 \
    src/main/java11/com/acme/greeter/RuntimeGreeting.java

python3 tools/assemble.py \
    build/classes/base \
    build/classes/java11 \
    src/main/resources \
    build/libs/runtime-greeter.jar

plugins {
    java
}

repositories {
    mavenCentral()
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

dependencies {
    testImplementation(platform("org.junit:junit-bom:5.12.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
    // Like many real builds: the whole list is set, not appended to.
    jvmArgs = listOf("-Xmx512m", "-XX:+UseSerialGC", "-Duser.timezone=UTC")
    systemProperty("spring.profiles.active", "test")
}

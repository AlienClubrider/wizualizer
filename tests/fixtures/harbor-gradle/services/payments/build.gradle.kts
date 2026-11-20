plugins {
    id("harbor.spring-service")
}

dependencies {
    implementation(project(":common:ledger"))
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("org.springframework.boot:spring-boot-starter-aop")
    runtimeOnly("com.h2database:h2")
}

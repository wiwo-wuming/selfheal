pipeline {
    agent any

    environment {
        PYTHON_VERSION = '3.11'
        // Credentials should be configured in Jenkins:
        // OPENAI_API_KEY via 'openai-api-key' credential
    }

    options {
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10', daysToKeepStr: '30'))
        ansiColor('xterm')
    }

    stages {
        // -----------------------------------------------------------------
        // Stage 1: Checkout & Setup
        // -----------------------------------------------------------------
        stage('Setup') {
            steps {
                checkout scm

                script {
                    // Detect Python
                    if (isUnix()) {
                        sh 'python3 --version || python --version'
                    } else {
                        bat 'python --version'
                    }
                }

                sh '''
                    pip install -e ".[dev]"
                '''
            }
        }

        // -----------------------------------------------------------------
        // Stage 2: Run Test Suite
        // -----------------------------------------------------------------
        stage('Run Tests') {
            steps {
                script {
                    def status = sh(
                        script: 'python -m pytest tests/ -v --tb=short --junitxml=junit.xml 2>&1',
                        returnStatus: true
                    )
                    env.TESTS_FAILED = (status != 0) ? '1' : '0'
                    if (status != 0) {
                        currentBuild.result = 'UNSTABLE'
                    }
                }
                junit 'junit.xml'
            }
        }

        // -----------------------------------------------------------------
        // Stage 3: Self-Heal (conditional on failure)
        // -----------------------------------------------------------------
        stage('Self-Heal Repair') {
            when {
                expression { env.TESTS_FAILED == '1' }
            }
            steps {
                script {
                    // Always install LLM support for the self-heal stage
                    sh 'pip install -e ".[llm]"'

                    withCredentials([string(credentialsId: 'openai-api-key', variable: 'OPENAI_API_KEY')]) {
                        sh '''
                            python -m selfheal batch --auto-apply --config selfheal.yaml 2>&1 || true
                        '''
                    }
                }
            }
        }

        // -----------------------------------------------------------------
        // Stage 4: Retry Tests
        // -----------------------------------------------------------------
        stage('Retry Tests') {
            when {
                expression { env.TESTS_FAILED == '1' }
            }
            steps {
                script {
                    def status = sh(
                        script: 'python -m pytest tests/ -v --tb=short --junitxml=junit-postfix.xml 2>&1',
                        returnStatus: true
                    )
                    env.POSTFIX_FAILED = (status != 0) ? '1' : '0'
                    if (status == 0) {
                        currentBuild.result = 'SUCCESS'
                    } else {
                        currentBuild.result = 'FAILURE'
                    }
                }
                junit 'junit-postfix.xml'
            }
        }

        // -----------------------------------------------------------------
        // Stage 5: Generate Metrics
        // -----------------------------------------------------------------
        stage('Metrics Report') {
            steps {
                sh '''
                    python -m selfheal metrics --json > selfheal-metrics.json
                '''
                archiveArtifacts artifacts: 'selfheal-metrics.json', allowEmptyArchive: true

                script {
                    if (fileExists('selfheal-metrics.json')) {
                        def metrics = readJSON file: 'selfheal-metrics.json'
                        echo "SelfHeal Metrics: ${metrics}"
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------------
    // Post-build actions
    // -------------------------------------------------------------------
    post {
        always {
            // Cleanup
            sh 'rm -f pytest-results.json junit.xml junit-postfix.xml 2>/dev/null || true'
            echo "Pipeline finished with result: ${currentBuild.result}"
        }
        success {
            echo '✅ All tests passed or self-heal succeeded!'
        }
        unstable {
            echo '⚠️ Test failures detected and repair initiated.'
        }
        failure {
            echo '❌ Tests still failing after self-heal attempt.'
        }
    }
}

import { Link } from 'react-router-dom';
import '../styles/PrivacyPolicy.css';

const EFFECTIVE_DATE = '2026년 7월 18일';
const CONTACT_EMAIL = 'shdlehdwls@gmail.com';

export const PrivacyPolicy = () => {
  return (
    <div className="policy-container">
      <div className="policy-box">
        <div className="policy-logo">Reco-Act</div>
        <h1 className="policy-title">개인정보처리방침</h1>
        <p className="policy-updated">시행일자: {EFFECTIVE_DATE}</p>

        <p className="policy-intro">
          Reco-Act(이하 "서비스")는 이용자의 개인정보를 중요하게 생각하며, 「개인정보 보호법」 등 관련 법령을
          준수하고 있습니다. 서비스는 본 개인정보처리방침을 통해 이용자가 제공하는 개인정보가 어떠한 목적과
          방식으로 이용되고 있으며, 개인정보 보호를 위해 어떠한 조치가 취해지고 있는지 안내합니다.
        </p>

        <section className="policy-section">
          <h2>1. 수집하는 개인정보 항목 및 수집 방법</h2>
          <p>서비스는 회원가입, 서비스 이용 과정에서 아래와 같은 개인정보를 수집합니다.</p>
          <table className="policy-table">
            <thead>
              <tr>
                <th>구분</th>
                <th>수집 항목</th>
                <th>수집 시점</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>회원가입 및 인증</td>
                <td>이메일 주소, 이름, 비밀번호(암호화 저장)</td>
                <td>회원가입 시</td>
              </tr>
              <tr>
                <td>서비스 이용 기록</td>
                <td>회의 녹음 음성파일, 음성 전사(STT) 텍스트, 화자 구분 정보, 회의 제목·참석자명·요약 결과</td>
                <td>회의 녹음 및 회의록 작성 시</td>
              </tr>
              <tr>
                <td>자동 수집 정보</td>
                <td>접속 로그, 서비스 이용 기록, 기기 정보(OS 버전 등)</td>
                <td>서비스 이용 시</td>
              </tr>
            </tbody>
          </table>
          <p>
            개인정보는 회원가입 화면 및 서비스 내 입력 폼을 통해 이용자가 직접 입력하는 방식, 그리고 모바일
            앱의 마이크 접근 권한을 통해 녹음이 이루어지는 방식으로 수집됩니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>2. 개인정보의 수집 및 이용 목적</h2>
          <ul>
            <li>회원 가입의사 확인, 본인 식별·인증, 회원자격 유지·관리</li>
            <li>회의 음성의 텍스트 전사(STT) 및 AI 기반 회의 요약 제공</li>
            <li>회의록 저장·조회·수정 등 서비스 핵심 기능 제공</li>
            <li>비밀번호 재설정 등 계정 관련 이메일 발송</li>
            <li>서비스 부정이용 방지 및 오류 개선을 위한 통계 분석</li>
          </ul>
        </section>

        <section className="policy-section">
          <h2>3. 개인정보의 보유 및 이용 기간</h2>
          <p>
            서비스는 원칙적으로 개인정보 수집 및 이용목적이 달성된 후에는 해당 정보를 지체 없이 파기합니다.
            다만, 이용자가 직접 회원 탈퇴를 요청하는 경우 계정 정보 및 회의 녹음·전사·요약 데이터를 지체 없이
            삭제하며, 관계 법령에 따라 보존이 필요한 경우 해당 법령에서 정한 기간 동안 보관합니다.
          </p>
          <ul>
            <li>회원 정보: 회원 탈퇴 시까지 (탈퇴 즉시 파기)</li>
            <li>회의 녹음파일, 전사 텍스트, 요약 결과: 이용자가 삭제하거나 회원 탈퇴 시까지</li>
          </ul>
        </section>

        <section className="policy-section">
          <h2>4. 개인정보의 파기 절차 및 방법</h2>
          <p>
            이용자가 회원 탈퇴를 요청하거나 보유 기간이 경과한 경우, 전자적 파일 형태의 정보는 복구가 불가능한
            방법으로 즉시 삭제합니다. 종이 문서 형태로 출력된 개인정보는 존재하지 않습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>5. 개인정보 처리의 위탁</h2>
          <p>서비스는 원활한 서비스 제공을 위해 아래와 같이 개인정보 처리업무를 외부 업체에 위탁하고 있습니다.</p>
          <table className="policy-table">
            <thead>
              <tr>
                <th>수탁업체</th>
                <th>위탁업무 내용</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>네이버클라우드 (CLOVA Speech)</td>
                <td>회의 음성파일의 텍스트 변환(STT) 처리</td>
              </tr>
              <tr>
                <td>Microsoft (Azure OpenAI)</td>
                <td>전사 텍스트 기반 AI 회의 요약 생성</td>
              </tr>
            </tbody>
          </table>
          <p>
            서비스는 위탁계약 체결 시 개인정보 보호법에 따라 위탁업무 수행목적 외 개인정보 처리금지, 기술적·관리적
            보호조치, 재위탁 제한 등을 계약서 등을 통해 규정하고 있습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>6. 개인정보의 제3자 제공</h2>
          <p>
            서비스는 이용자의 개인정보를 원칙적으로 외부에 제공하지 않습니다. 다만, 이용자가 사전에 동의하거나
            법령의 규정에 의거한 경우, 수사기관이 적법한 절차에 따라 요청하는 경우에는 예외로 합니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>7. 이용자 및 법정대리인의 권리와 행사 방법</h2>
          <p>
            이용자는 언제든지 서비스 내 "프로필" 화면을 통해 등록된 개인정보를 조회·수정할 수 있으며, 회원
            탈퇴를 통해 개인정보의 수집 및 이용 동의를 철회할 수 있습니다. 회원 탈퇴는 앱 내 [프로필 → 회원
            탈퇴] 메뉴에서 직접 처리할 수 있으며, 아래 연락처를 통해서도 삭제를 요청할 수 있습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>8. 앱 접근 권한 안내</h2>
          <p>서비스는 모바일 앱(Android) 이용 시 아래 권한에 대한 접근을 요청할 수 있습니다.</p>
          <table className="policy-table">
            <thead>
              <tr>
                <th>권한</th>
                <th>목적</th>
                <th>필수 여부</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>마이크(RECORD_AUDIO)</td>
                <td>회의 음성 녹음</td>
                <td>필수</td>
              </tr>
              <tr>
                <td>알림(POST_NOTIFICATIONS)</td>
                <td>녹음 진행 상태 등 알림 표시</td>
                <td>선택</td>
              </tr>
            </tbody>
          </table>
          <p>선택 권한은 동의하지 않아도 관련 기능을 제외한 서비스 이용이 가능합니다.</p>
        </section>

        <section className="policy-section">
          <h2>9. 개인정보의 안전성 확보 조치</h2>
          <ul>
            <li>비밀번호는 암호화하여 저장하며, 통신 구간은 HTTPS를 통해 암호화됩니다.</li>
            <li>서비스 접근을 위한 인증에는 JWT 기반 접근 토큰 및 갱신 토큰을 사용합니다.</li>
            <li>개인정보에 대한 접근 권한은 서비스 운영에 필요한 최소한의 범위로 제한합니다.</li>
          </ul>
        </section>

        <section className="policy-section">
          <h2>10. 개인정보 보호책임자</h2>
          <p>
            서비스는 개인정보 처리에 관한 업무를 총괄하고 이용자의 불만처리 및 피해구제를 위해 아래와 같이
            개인정보 보호책임자를 지정하고 있습니다.
          </p>
          <div className="policy-contact">
            개인정보 보호책임자: Reco-Act 개발자
            <br />
            이메일: {CONTACT_EMAIL}
          </div>
        </section>

        <section className="policy-section">
          <h2>11. 고지의 의무</h2>
          <p>
            본 개인정보처리방침의 내용이 추가, 삭제 및 수정이 있을 경우 서비스 내 공지사항 또는 본 페이지를
            통해 사전에 고지합니다.
          </p>
        </section>

        <p className="policy-updated">시행일자: {EFFECTIVE_DATE}</p>

        <div className="policy-footer">
          <Link to="/login" className="policy-back-link">로그인 화면으로 돌아가기</Link>
        </div>
      </div>
    </div>
  );
};

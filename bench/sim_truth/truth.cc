// Bench-only read-only telemetry. Existing Gazebo APIs (Apache-2.0 dependencies).
#include <gz/sim/System.hh>
#include <gz/sim/Actor.hh>
#include <gz/sim/Util.hh>
#include <gz/sim/components/Actor.hh>
#include <gz/sim/components/Name.hh>
#include <gz/sim/components/Pose.hh>
#include <gz/plugin/Register.hh>
#include <gz/transport/Node.hh>
#include <gz/msgs/pose_v.pb.h>
#include <gz/msgs/Utility.hh>
class BenchTruth : public gz::sim::System, public gz::sim::ISystemPostUpdate {
 gz::transport::Node node;
 gz::transport::Node::Publisher pub=node.Advertise<gz::msgs::Pose_V>("/garuda/bench/truth");
 std::chrono::steady_clock::duration last{};
 public: void PostUpdate(const gz::sim::UpdateInfo &info,const gz::sim::EntityComponentManager &ecm) override {
  if(info.paused || info.simTime-last<std::chrono::milliseconds(50)) return;
  last=info.simTime;gz::msgs::Pose_V out;
  auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(info.simTime).count();
  out.mutable_header()->mutable_stamp()->set_sec(ns/1000000000);
  out.mutable_header()->mutable_stamp()->set_nsec(ns%1000000000);
  ecm.Each<gz::sim::components::Name>([&](auto entity,const auto *name){
   if(name->Data()!="walker" && name->Data()!="garuda_x500")return true;
   std::optional<gz::math::Pose3d> pose;
   if(name->Data()=="walker")pose=gz::sim::Actor(entity).WorldPose(ecm);
   else pose=gz::sim::worldPose(entity,ecm);
   if(pose){auto *p=out.add_pose();gz::msgs::Set(p,*pose);p->set_name(name->Data());p->set_id(entity);}
   return true;
  });
  pub.Publish(out);
 }
};
GZ_ADD_PLUGIN(BenchTruth,gz::sim::System,gz::sim::ISystemPostUpdate)
